/*
 * forge-trace.so: records the files a program and everything it starts
 * open, execute or look for (docs/designs/0005).
 *
 * Loaded with LD_PRELOAD, with FORGE_TRACE_FILE naming the record.
 * Each call it wraps appends one line, "<kind> <absolute path>\n", in a
 * single write to a descriptor opened with O_APPEND, so lines from
 * many processes and threads never interleave. The kind is "x" for a
 * program executed (or about to be), "o" for anything else. A path is
 * recorded whether or not the call succeeds: a file looked for and not
 * found can change what a step does once it appears.
 *
 * Every process must keep the tracer. LD_PRELOAD and FORGE_TRACE_FILE
 * are inherited across fork and exec like any variable, and each exec*
 * and posix_spawn* wrapper puts both back into an environment a
 * program passes without them (`env -i`, Python's subprocess with
 * env=). system() and popen() run with the process's own environment,
 * so they put them back into that first.
 *
 * It sees only calls made through the C library: a statically linked
 * program, or one making system calls itself, goes unrecorded, which
 * is why forge-pr-check checks that a record is plausible.
 *
 * Built by forge-pr-check with: cc -shared -fPIC -O2 -o forge-trace.so
 * forge-trace.c -ldl (agent_forge/trace.py's CFLAGS)
 */

#define _GNU_SOURCE
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <spawn.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#define TRACE_VAR "FORGE_TRACE_FILE"
#define PRELOAD_VAR "LD_PRELOAD"

extern char **environ;

/* Set once, in the constructor: this library's path, the record's
 * path, and "NAME=value" strings to put back into an environment. */
static char self_path[PATH_MAX];
static char trace_path[PATH_MAX];
static char trace_entry[PATH_MAX + sizeof(TRACE_VAR) + 1];
static int active;

/* The record's descriptor, and what it was when opened: a program may
 * close it (Python closes every descriptor before an exec), and a later
 * open may reuse its number for something else. */
static int trace_fd = -1;
static dev_t trace_dev;
static ino_t trace_ino;

/* `p`, which the compiler may no longer assume isn't null. The C
 * library declares many of the calls wrapped here `nonnull`, and the
 * wrappers inherit that, so a plain test of the path is optimised
 * away; but a program may pass null on purpose (Rust's standard
 * library calls statx(0, NULL, ...) to learn whether statx works). */
#define OPAQUE(p) ({ __typeof__(p) _p = (p); __asm__("" : "+r"(_p)); _p; })

#define REAL(name) real_##name
#define DECLARE(ret, name, ...) static ret (*real_##name)(__VA_ARGS__)

DECLARE(int, open, const char *, int, ...);
DECLARE(int, open64, const char *, int, ...);
DECLARE(int, openat, int, const char *, int, ...);
DECLARE(int, openat64, int, const char *, int, ...);
DECLARE(int, __open_2, const char *, int);
DECLARE(int, __open64_2, const char *, int);
DECLARE(int, __openat_2, int, const char *, int);
DECLARE(int, __openat64_2, int, const char *, int);
DECLARE(int, creat, const char *, mode_t);
DECLARE(int, creat64, const char *, mode_t);
DECLARE(FILE *, fopen, const char *, const char *);
DECLARE(FILE *, fopen64, const char *, const char *);
DECLARE(FILE *, freopen, const char *, const char *, FILE *);
DECLARE(FILE *, freopen64, const char *, const char *, FILE *);
DECLARE(int, stat, const char *, struct stat *);
DECLARE(int, lstat, const char *, struct stat *);
DECLARE(int, stat64, const char *, struct stat64 *);
DECLARE(int, lstat64, const char *, struct stat64 *);
DECLARE(int, fstatat, int, const char *, struct stat *, int);
DECLARE(int, fstatat64, int, const char *, struct stat64 *, int);
DECLARE(int, __xstat, int, const char *, struct stat *);
DECLARE(int, __lxstat, int, const char *, struct stat *);
DECLARE(int, __xstat64, int, const char *, struct stat64 *);
DECLARE(int, __lxstat64, int, const char *, struct stat64 *);
DECLARE(int, __fxstatat, int, int, const char *, struct stat *, int);
DECLARE(int, __fxstatat64, int, int, const char *, struct stat64 *, int);
DECLARE(int, statx, int, const char *, int, unsigned int, struct statx *);
DECLARE(int, access, const char *, int);
DECLARE(int, faccessat, int, const char *, int, int);
DECLARE(int, euidaccess, const char *, int);
DECLARE(int, eaccess, const char *, int);
DECLARE(DIR *, opendir, const char *);
DECLARE(ssize_t, readlink, const char *, char *, size_t);
DECLARE(int, execve, const char *, char *const[], char *const[]);
DECLARE(int, execvpe, const char *, char *const[], char *const[]);
DECLARE(int, fexecve, int, char *const[], char *const[]);
DECLARE(int, posix_spawn, pid_t *, const char *,
        const posix_spawn_file_actions_t *, const posix_spawnattr_t *,
        char *const[], char *const[]);
DECLARE(int, posix_spawnp, pid_t *, const char *,
        const posix_spawn_file_actions_t *, const posix_spawnattr_t *,
        char *const[], char *const[]);
DECLARE(int, system, const char *);
DECLARE(FILE *, popen, const char *, const char *);

#define RESOLVE(name) \
    do { \
        if (!real_##name) \
            real_##name = (__typeof__(real_##name))dlsym(RTLD_NEXT, #name); \
    } while (0)

static void resolve_all(void)
{
    RESOLVE(open); RESOLVE(open64); RESOLVE(openat); RESOLVE(openat64);
    RESOLVE(__open_2); RESOLVE(__open64_2); RESOLVE(__openat_2);
    RESOLVE(__openat64_2); RESOLVE(creat); RESOLVE(creat64);
    RESOLVE(fopen); RESOLVE(fopen64); RESOLVE(freopen); RESOLVE(freopen64);
    RESOLVE(stat); RESOLVE(lstat); RESOLVE(stat64); RESOLVE(lstat64);
    RESOLVE(fstatat); RESOLVE(fstatat64); RESOLVE(__xstat);
    RESOLVE(__lxstat); RESOLVE(__xstat64); RESOLVE(__lxstat64);
    RESOLVE(__fxstatat); RESOLVE(__fxstatat64); RESOLVE(statx);
    RESOLVE(access); RESOLVE(faccessat); RESOLVE(euidaccess);
    RESOLVE(eaccess); RESOLVE(opendir); RESOLVE(readlink);
    RESOLVE(execve); RESOLVE(execvpe); RESOLVE(fexecve);
    RESOLVE(posix_spawn); RESOLVE(posix_spawnp); RESOLVE(system);
    RESOLVE(popen);
}

/* Appends `src` to dst[*len], keeping within `cap`. */
static void append(char *dst, size_t *len, size_t cap, const char *src)
{
    while (*src && *len + 1 < cap)
        dst[(*len)++] = *src++;
    dst[*len] = '\0';
}

/* The record's descriptor, opened again if it was closed or replaced. */
static int trace_descriptor(void)
{
    struct stat now;
    if (trace_fd >= 0 && fstat(trace_fd, &now) == 0
            && now.st_dev == trace_dev && now.st_ino == trace_ino)
        return trace_fd;
    RESOLVE(open);
    if (!real_open)
        return -1;
    int fd = real_open(trace_path, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC,
                       0644);
    if (fd < 0)
        return -1;
    if (fstat(fd, &now) != 0) {
        close(fd);
        return -1;
    }
    trace_fd = fd;
    trace_dev = now.st_dev;
    trace_ino = now.st_ino;
    return fd;
}

/* Records `path`, relative to `dirfd` (AT_FDCWD for the working
 * directory). No allocation: it runs in vfork children too. */
static void record(char kind, int dirfd, const char *path)
{
    path = OPAQUE(path);
    if (!active || !path || !*path)
        return;
    int saved = errno;
    char line[2 * PATH_MAX + 8];
    size_t len = 0;
    line[len++] = kind;
    line[len++] = ' ';
    line[len] = '\0';
    if (path[0] != '/') {
        char base[PATH_MAX];
        base[0] = '\0';
        if (dirfd == AT_FDCWD) {
            if (!getcwd(base, sizeof base))
                base[0] = '\0';
        } else {
            char link[64];
            size_t n = 0;
            append(link, &n, sizeof link, "/proc/self/fd/");
            char digits[16];
            int d = 0, v = dirfd < 0 ? 0 : dirfd;
            do {
                digits[d++] = (char)('0' + v % 10);
                v /= 10;
            } while (v && d < (int)sizeof digits);
            while (d)
                link[n++] = digits[--d];
            link[n] = '\0';
            RESOLVE(readlink);
            ssize_t got = real_readlink ? real_readlink(link, base,
                                                        sizeof base - 1) : -1;
            base[got > 0 ? got : 0] = '\0';
        }
        append(line, &len, sizeof line - 1, base);
        append(line, &len, sizeof line - 1, "/");
    }
    append(line, &len, sizeof line - 1, path);
    line[len++] = '\n';
    int fd = trace_descriptor();
    if (fd >= 0) {
        ssize_t ignored = write(fd, line, len);
        (void)ignored;
    }
    errno = saved;
}

/* Whether `entry` ("NAME=...") is for `name`. */
static int is_var(const char *entry, const char *name)
{
    size_t n = strlen(name);
    return strncmp(entry, name, n) == 0 && entry[n] == '=';
}

/* Whether the LD_PRELOAD value `value` already lists this library. */
static int lists_self(const char *value)
{
    size_t n = strlen(self_path);
    for (const char *p = value; (p = strstr(p, self_path)); p += n) {
        int starts = p == value || p[-1] == ':' || p[-1] == ' ';
        int ends = p[n] == '\0' || p[n] == ':' || p[n] == ' ';
        if (starts && ends)
            return 1;
    }
    return 0;
}

/* How many entries `envp` has. */
static size_t count(char *const envp[])
{
    size_t n = 0;
    while (envp && envp[n])
        n++;
    return n;
}

/* The longest LD_PRELOAD entry `envp` could need. */
static size_t preload_room(char *const envp[])
{
    size_t room = sizeof(PRELOAD_VAR) + strlen(self_path) + 2;
    for (size_t i = 0; envp && envp[i]; i++)
        if (is_var(envp[i], PRELOAD_VAR))
            room += strlen(envp[i]);
    return room;
}

/* Fills `out` (count(envp) + 3 entries) with `envp` less any wrong
 * FORGE_TRACE_FILE, with this library in LD_PRELOAD (built in
 * `preload`, preload_room(envp) bytes) and the record named. Returns
 * `out`. */
static char **keep_tracer(char *const envp[], char **out, char *preload)
{
    size_t n = 0;
    int have_preload = 0;
    for (size_t i = 0; envp && envp[i]; i++) {
        if (is_var(envp[i], TRACE_VAR))
            continue;
        if (is_var(envp[i], PRELOAD_VAR)) {
            const char *value = envp[i] + sizeof(PRELOAD_VAR);
            if (lists_self(value)) {
                out[n++] = envp[i];
            } else {
                size_t len = 0;
                preload[0] = '\0';
                append(preload, &len, (size_t)-1, PRELOAD_VAR "=");
                append(preload, &len, (size_t)-1, self_path);
                if (*value) {
                    append(preload, &len, (size_t)-1, ":");
                    append(preload, &len, (size_t)-1, value);
                }
                out[n++] = preload;
            }
            have_preload = 1;
            continue;
        }
        out[n++] = envp[i];
    }
    if (!have_preload) {
        size_t len = 0;
        preload[0] = '\0';
        append(preload, &len, (size_t)-1, PRELOAD_VAR "=");
        append(preload, &len, (size_t)-1, self_path);
        out[n++] = preload;
    }
    out[n++] = trace_entry;
    out[n] = NULL;
    return out;
}

/* Declares `name`, envp with the tracer kept, on the caller's stack. */
#define KEPT(name, envp) \
    char *name##_list[count(envp) + 3]; \
    char name##_preload[preload_room(envp)]; \
    char *const *name = active \
        ? keep_tracer((envp), name##_list, name##_preload) : (envp)

/* Puts the tracer back into this process's own environment. */
static void keep_tracer_here(void)
{
    if (!active)
        return;
    const char *value = getenv(PRELOAD_VAR);
    if (!value || !lists_self(value)) {
        size_t room = strlen(self_path) + (value ? strlen(value) : 0) + 2;
        char preload[room];
        size_t len = 0;
        preload[0] = '\0';
        append(preload, &len, room, self_path);
        if (value && *value) {
            append(preload, &len, room, ":");
            append(preload, &len, room, value);
        }
        setenv(PRELOAD_VAR, preload, 1);
    }
    setenv(TRACE_VAR, trace_path, 1);
}

__attribute__((constructor)) static void start(void)
{
    resolve_all();
    const char *path = getenv(TRACE_VAR);
    Dl_info info;
    if (!path || !*path || strlen(path) >= sizeof trace_path
            || !dladdr((void *)start, &info) || !info.dli_fname
            || strlen(info.dli_fname) >= sizeof self_path)
        return;
    size_t len = 0;
    append(trace_path, &len, sizeof trace_path, path);
    len = 0;
    append(self_path, &len, sizeof self_path, info.dli_fname);
    len = 0;
    append(trace_entry, &len, sizeof trace_entry, TRACE_VAR "=");
    append(trace_entry, &len, sizeof trace_entry, trace_path);
    active = 1;
    /* The program this process is running, however it was started. */
    char exe[PATH_MAX];
    ssize_t got = real_readlink ? real_readlink("/proc/self/exe", exe,
                                                sizeof exe - 1) : -1;
    if (got > 0) {
        exe[got] = '\0';
        record('x', AT_FDCWD, exe);
    }
}

/* open and its relatives: the mode is there only with O_CREAT or
 * O_TMPFILE, as the C library reads it. */
#define MODE(flags) \
    mode_t mode = 0; \
    if ((flags) & (O_CREAT | __O_TMPFILE)) { \
        va_list ap; \
        va_start(ap, flags); \
        mode = (mode_t)va_arg(ap, int); \
        va_end(ap); \
    }

int open(const char *path, int flags, ...)
{
    MODE(flags);
    RESOLVE(open);
    record('o', AT_FDCWD, path);
    return real_open(path, flags, mode);
}

int open64(const char *path, int flags, ...)
{
    MODE(flags);
    RESOLVE(open64);
    record('o', AT_FDCWD, path);
    return real_open64(path, flags, mode);
}

int openat(int dirfd, const char *path, int flags, ...)
{
    MODE(flags);
    RESOLVE(openat);
    record('o', dirfd, path);
    return real_openat(dirfd, path, flags, mode);
}

int openat64(int dirfd, const char *path, int flags, ...)
{
    MODE(flags);
    RESOLVE(openat64);
    record('o', dirfd, path);
    return real_openat64(dirfd, path, flags, mode);
}

int __open_2(const char *path, int flags)
{
    RESOLVE(__open_2);
    record('o', AT_FDCWD, path);
    return real___open_2(path, flags);
}

int __open64_2(const char *path, int flags)
{
    RESOLVE(__open64_2);
    record('o', AT_FDCWD, path);
    return real___open64_2(path, flags);
}

int __openat_2(int dirfd, const char *path, int flags)
{
    RESOLVE(__openat_2);
    record('o', dirfd, path);
    return real___openat_2(dirfd, path, flags);
}

int __openat64_2(int dirfd, const char *path, int flags)
{
    RESOLVE(__openat64_2);
    record('o', dirfd, path);
    return real___openat64_2(dirfd, path, flags);
}

int creat(const char *path, mode_t mode)
{
    RESOLVE(creat);
    record('o', AT_FDCWD, path);
    return real_creat(path, mode);
}

int creat64(const char *path, mode_t mode)
{
    RESOLVE(creat64);
    record('o', AT_FDCWD, path);
    return real_creat64(path, mode);
}

FILE *fopen(const char *path, const char *how)
{
    RESOLVE(fopen);
    record('o', AT_FDCWD, path);
    return real_fopen(path, how);
}

FILE *fopen64(const char *path, const char *how)
{
    RESOLVE(fopen64);
    record('o', AT_FDCWD, path);
    return real_fopen64(path, how);
}

FILE *freopen(const char *path, const char *how, FILE *stream)
{
    RESOLVE(freopen);
    record('o', AT_FDCWD, path);
    return real_freopen(path, how, stream);
}

FILE *freopen64(const char *path, const char *how, FILE *stream)
{
    RESOLVE(freopen64);
    record('o', AT_FDCWD, path);
    return real_freopen64(path, how, stream);
}

int stat(const char *path, struct stat *buf)
{
    RESOLVE(stat);
    record('o', AT_FDCWD, path);
    return real_stat(path, buf);
}

int lstat(const char *path, struct stat *buf)
{
    RESOLVE(lstat);
    record('o', AT_FDCWD, path);
    return real_lstat(path, buf);
}

int stat64(const char *path, struct stat64 *buf)
{
    RESOLVE(stat64);
    record('o', AT_FDCWD, path);
    return real_stat64(path, buf);
}

int lstat64(const char *path, struct stat64 *buf)
{
    RESOLVE(lstat64);
    record('o', AT_FDCWD, path);
    return real_lstat64(path, buf);
}

int fstatat(int dirfd, const char *path, struct stat *buf, int flags)
{
    RESOLVE(fstatat);
    record('o', dirfd, path);
    return real_fstatat(dirfd, path, buf, flags);
}

int fstatat64(int dirfd, const char *path, struct stat64 *buf, int flags)
{
    RESOLVE(fstatat64);
    record('o', dirfd, path);
    return real_fstatat64(dirfd, path, buf, flags);
}

/* The C library's own names for stat before 2.33. */
int __xstat(int ver, const char *path, struct stat *buf)
{
    RESOLVE(__xstat);
    record('o', AT_FDCWD, path);
    return real___xstat(ver, path, buf);
}

int __lxstat(int ver, const char *path, struct stat *buf)
{
    RESOLVE(__lxstat);
    record('o', AT_FDCWD, path);
    return real___lxstat(ver, path, buf);
}

int __xstat64(int ver, const char *path, struct stat64 *buf)
{
    RESOLVE(__xstat64);
    record('o', AT_FDCWD, path);
    return real___xstat64(ver, path, buf);
}

int __lxstat64(int ver, const char *path, struct stat64 *buf)
{
    RESOLVE(__lxstat64);
    record('o', AT_FDCWD, path);
    return real___lxstat64(ver, path, buf);
}

int __fxstatat(int ver, int dirfd, const char *path, struct stat *buf,
               int flags)
{
    RESOLVE(__fxstatat);
    record('o', dirfd, path);
    return real___fxstatat(ver, dirfd, path, buf, flags);
}

int __fxstatat64(int ver, int dirfd, const char *path, struct stat64 *buf,
                 int flags)
{
    RESOLVE(__fxstatat64);
    record('o', dirfd, path);
    return real___fxstatat64(ver, dirfd, path, buf, flags);
}

int statx(int dirfd, const char *path, int flags, unsigned int mask,
          struct statx *buf)
{
    RESOLVE(statx);
    /* An empty path with AT_EMPTY_PATH is a descriptor, not a lookup.
     * A null one is Rust's standard library asking, after a failed
     * stat, whether statx works at all: it expects EFAULT, not a crash
     * here (cargo did, looking for a config file that isn't there). */
    const char *p = OPAQUE(path);
    if (p && *p)
        record('o', dirfd, p);
    return real_statx(dirfd, path, flags, mask, buf);
}

int access(const char *path, int how)
{
    RESOLVE(access);
    record('o', AT_FDCWD, path);
    return real_access(path, how);
}

int faccessat(int dirfd, const char *path, int how, int flags)
{
    RESOLVE(faccessat);
    record('o', dirfd, path);
    return real_faccessat(dirfd, path, how, flags);
}

int euidaccess(const char *path, int how)
{
    RESOLVE(euidaccess);
    record('o', AT_FDCWD, path);
    return real_euidaccess(path, how);
}

int eaccess(const char *path, int how)
{
    RESOLVE(eaccess);
    record('o', AT_FDCWD, path);
    return real_eaccess(path, how);
}

DIR *opendir(const char *path)
{
    RESOLVE(opendir);
    record('o', AT_FDCWD, path);
    return real_opendir(path);
}

int execve(const char *path, char *const argv[], char *const envp[])
{
    RESOLVE(execve);
    record('x', AT_FDCWD, path);
    KEPT(env, envp);
    return real_execve(path, argv, env);
}

int execvpe(const char *file, char *const argv[], char *const envp[])
{
    RESOLVE(execvpe);
    /* A name without a slash is looked for on PATH; the program found
     * records itself when it starts. */
    if (strchr(file, '/'))
        record('x', AT_FDCWD, file);
    KEPT(env, envp);
    return real_execvpe(file, argv, env);
}

int execv(const char *path, char *const argv[])
{
    return execve(path, argv, environ);
}

int execvp(const char *file, char *const argv[])
{
    return execvpe(file, argv, environ);
}

int fexecve(int fd, char *const argv[], char *const envp[])
{
    RESOLVE(fexecve);
    KEPT(env, envp);
    return real_fexecve(fd, argv, env);
}

/* execl and its relatives, as their v forms. */
#define ARGV(first, ap) \
    size_t argc = 1; \
    va_start(ap, first); \
    while (va_arg(ap, char *)) \
        argc++; \
    va_end(ap); \
    char *args[argc + 1]; \
    args[0] = (char *)(first); \
    va_start(ap, first); \
    for (size_t i = 1; i <= argc; i++) \
        args[i] = va_arg(ap, char *);

int execl(const char *path, const char *arg, ...)
{
    va_list ap;
    ARGV(arg, ap);
    va_end(ap);
    return execve(path, args, environ);
}

int execlp(const char *file, const char *arg, ...)
{
    va_list ap;
    ARGV(arg, ap);
    va_end(ap);
    return execvpe(file, args, environ);
}

int execle(const char *path, const char *arg, ...)
{
    va_list ap;
    ARGV(arg, ap);
    char *const *envp = va_arg(ap, char *const *);
    va_end(ap);
    return execve(path, args, envp);
}

int posix_spawn(pid_t *pid, const char *path,
                const posix_spawn_file_actions_t *actions,
                const posix_spawnattr_t *attr, char *const argv[],
                char *const envp[])
{
    RESOLVE(posix_spawn);
    record('x', AT_FDCWD, path);
    KEPT(env, envp);
    return real_posix_spawn(pid, path, actions, attr, argv, env);
}

int posix_spawnp(pid_t *pid, const char *file,
                 const posix_spawn_file_actions_t *actions,
                 const posix_spawnattr_t *attr, char *const argv[],
                 char *const envp[])
{
    RESOLVE(posix_spawnp);
    if (strchr(file, '/'))
        record('x', AT_FDCWD, file);
    KEPT(env, envp);
    return real_posix_spawnp(pid, file, actions, attr, argv, env);
}

int system(const char *command)
{
    RESOLVE(system);
    keep_tracer_here();
    return real_system(command);
}

FILE *popen(const char *command, const char *how)
{
    RESOLVE(popen);
    keep_tracer_here();
    return real_popen(command, how);
}
