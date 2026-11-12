# forge-install

`forge-install` keeps a host's agent-forge installation at the commit
the project pins in `.agent-forge/pin`. `forge-cron-run` refuses to run
any tool from an installation at another commit (`PIN-MISMATCH`), so
after a pin bump every scheduled job on the host waits, up to
`FORGE_CRON_PIN_WAIT_S` (10 minutes by default), and then stops, until
the installation moves. This tool moves it, between jobs or beside them, so
nobody has to log in to each host and check out the new release by
hand.

## Usage

    forge-install [--checkout DIR] [--install DIR] [--url URL]
                  [--wait SECONDS] [--versions] [--bin DIR] [--dry-run]

Run it from cron every minute or every few minutes. Run it directly,
**not** through `forge-cron-run`: that wrapper refuses to start anything
while the installation is off the pin, which is exactly the state this
tool is there to end.

    * * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/share/agent-forge/bin/forge-install --versions

Call it by the installation's own path, not through a link in
`~/.local/bin`, so the crontab still works on the first install. A run
with nothing to do reads only the pin and the installation, so running
it every minute is cheap. To see what it would do, run it by hand with
`--dry-run`.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--checkout DIR` | `$FORGE_CRON_CHECKOUT` | The project's cron checkout, whose `.agent-forge/pin` is followed. With neither, the tool exits 2. |
| `--install DIR` | this installation | The installation to move. When the tool runs from a release inside `<install>.d`, the default is the `<install>` symlink, not the release. |
| `--url URL` | agent-forge's own repository on GitHub, as in the README's `git clone` | Where agent-forge is cloned from. An installation whose `origin` is anything else is refused. A trailing `/` or `.git` doesn't count as a difference. |
| `--wait SECONDS` | `240` | How long to wait for running jobs to finish before moving the installation in place. Not used by `--versions`, except on its first run (see below). |
| `--versions` | off | Install each release beside the running one, in `<install>.d/<sha>`, and switch a symlink. Nothing waits for jobs ([designs/0015](../designs/0015-install-beside-never-under.md)). |
| `--bin DIR` | `~/.local/bin` | Where the host links the tools. New tools get a link here (see below). |
| `--dry-run` | off | Say what would be done, and change nothing. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CRON_CHECKOUT` | The default for `--checkout`. |
| `HOME` | The home directory `--bin` defaults under. |

## Configuration

It reads no key of `.agent-forge/config.json`. It reads one other file
in the checkout: `.agent-forge/pin`
([config.md, The pin](../config.md#the-pin)). Its first word must be a
full 40-character commit SHA. Anything after it, such as the tag, is
for readers. A checkout with no pin file means nothing is installed.

The pin is read from the checkout as it is on disk. This tool doesn't
sync the checkout; the jobs that `forge-cron-run` runs from it do.

## What it does

Each run reads the pin, takes the installer lock, and then does one of
the following. It prints one line saying what it did.

**No pin.** It prints `NO-PIN` and exits 0.

**Another installer is running** for the same installation: it prints
`BUSY` and exits 0. So cron can start it every minute while an earlier
run still waits for jobs.

**It refuses rather than guesses.** It exits 2 and touches nothing when:

* the pin isn't a full SHA (`BAD-PIN`);
* the installation's `origin` isn't `--url` (`FOREIGN`);
* the installation has local changes (`DIRTY`);
* without `--versions`, the installation is a symlink (`VERSIONED`:
  run with `--versions`);
* with `--versions`, the symlink points at nothing (`BROKEN`).

**Only the pinned commit is checked out**, detached, never a tag or a
branch, since a tag can move. It fetches the SHA directly first, then
every ref if the server won't serve a SHA. A commit it can't fetch
leaves the installation unchanged and exits 1.

### In place (without `--versions`)

* **Already at the pin**: it prints `AT-PIN`.
* **No installation yet**: it clones into `<install>.new`, checks out
  the pin, and renames it into place, so a half-made clone is never the
  installation. It prints `INSTALLED`.
* **At another commit**: it fetches the pin while jobs go on running.
  Then it leaves the marker `<install>.install-wanted`, which makes
  `forge-cron-run` start no new job (`INSTALL-PENDING`). It takes the
  installation's lock, `<install>.lock`, exclusively. Each job
  `forge-cron-run` starts holds that lock shared for its whole run, and
  hands it on to what it starts, such as a build-check worker. So the
  exclusive lock means every job has finished. It then checks out the
  pin and removes the marker.
* **Jobs don't finish within `--wait` seconds**: it exits 1 and leaves
  the marker, so new jobs stay held off and the next run waits again.
  A marker more than an hour old was left by an installer that died,
  and `forge-cron-run` ignores it.

### Beside (with `--versions`)

The installation is a symlink to `<install>.d/<sha>`, one clean clone
per release.

* **First run on a plain directory**: it moves the directory into the
  layout once, the in-place way (the marker, then the wait for jobs, up
  to `--wait`). The directory becomes `<install>.d/<its HEAD>` and the
  symlink replaces it.
* **Already at the pin**: it prints `AT-PIN`, and deletes old releases
  that are due (see below).
* **At another commit**: it clones the current release into
  `<install>.d/<pin>.new`, so its objects are hard links, points its
  `origin` at `--url`, fetches and checks out the pin, and renames it
  to `<install>.d/<pin>`. Then it renames a new symlink over the old
  one, so a job always sees one release or the other. It prints
  `INSTALLED ... beside it`.
* Jobs that start afterwards run the new release. A running job keeps
  its own: each holds `<release>.lock` shared. `forge-cron-run` puts
  the job's own release first in `PATH`, so what the job runs by name
  comes from that release too.
* **Old releases.** The release that stopped being current is marked
  with `<release>.retired` (a timestamp). Once it has been retired for
  ten minutes and no job holds its lock, it is deleted. A release that
  isn't current and has no marker (left by a run that died before
  switching) is marked now. Deleted releases are named on the output
  line.
* A failed clone leaves the symlink where it was.

### New tools get links

When the `--bin` directory already links at least one tool into the
installation, every executable `forge-*` tool the installation has and
the directory lacks gets a symlink there. These are printed as
`LINKED`. Nothing already in the directory is replaced, and a directory
that links nothing into the installation is left alone. So a host set
up as the README says can run a new tool by name from its first
release.

## State and logs

All beside the installation path `<install>`:

| Path | What it is |
| --- | --- |
| `<install>.installer.lock` | One installer at a time. |
| `<install>.lock` | Held shared by every job, exclusively while moving in place. |
| `<install>.install-wanted` | Asks `forge-cron-run` to start no new job. Removed once moved; ignored after an hour. |
| `<install>.new` | A first clone being made, in place mode. |
| `<install>.d/<sha>` | One release, with `--versions`. |
| `<install>.d/<sha>.new` | A release being cloned. |
| `<install>.d/<sha>.lock` | Held shared by each job running that release. |
| `<install>.d/<sha>.retired` | When the release stopped being current. |
| `<install>.new-link` | The symlink being renamed into place. |

It writes no log of its own: output goes to stdout and errors to
stderr, so cron mails them or the crontab redirects them.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | At the pin, now installed, no pin, or busy. A dry run that would install exits 0 too. |
| 1 | A step failed (a fetch, a clone, a checkout), or the jobs didn't drain in `--wait` seconds. The installation is unchanged. |
| 2 | Refused: no checkout, a bad pin, a foreign or dirty installation, a symlink without `--versions`, or a broken symlink. Nothing was touched. |

## See also

* [config.md, The pin](../config.md#the-pin)
* [designs/0015](../designs/0015-install-beside-never-under.md),
  installing beside the running release
* [forge-config](forge-config.md)
