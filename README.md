# Unix Memory Vibemap

Vibe-coded with Anthropic Claude (Sonnet 5) using Python and PySide6.

A standalone desktop app that shows live process memory usage as a treemap, refreshing automatically. Runs on:

* **FreeBSD** – via `ps`, since FreeBSD has no `/proc` by default
* **Linux distros** with a GUI – via `/proc`, unchanged from earlier versions of this app.

The platform is detected automatically at startup; there is nothing to configure.

## Install

**Linux**:

```
pip install PySide6 --break-system-packages
```

**FreeBSD** – simplest is to install the PySide6 port directly; this needs no `pip` and no knowledge of Python packaging at all:

```sh
pkg install lang/python3 devel/pyside6
```

If you'd prefer to use `pip` on FreeBSD instead:

```sh
pkg install devel/py-pip
pip install PySide6
```

## Run

```
python3 unix-memory-vibemap.py
```

On FreeBSD, running as your normal user will show every process you have permission to see; running under `doas`/`sudo` will show more.

## Features

**Live refresh** – choose 5 / 15 / 30 / 60 seconds from the drop-down, or click "Refresh now" for an immediate update.

**Grouped apps** – Firefox (including its sandboxed "Web Content", "Isolated Web Co", etc. sub-processes), Chrome/Chromium, Thunderbird, VS Code, Java, Python, Node, and VLC all aggregate into a single block, so a multi-process browser shows as one meaningful rectangle instead of dozens of slivers.

**Consistent colours** – each app keeps the same colour across refreshes, assigned the first time it's seen.

**GB formatting** – usage at or above 1024 MB displays in GB (e.g. `1.5 GB`) for readability; smaller values stay in MB.

## How memory is read

| Platform | Method | Notes |
|----------|--------|-------|
| Linux    | `/proc/[pid]/status` (`VmRSS`) | Read directly in Python, no subprocess. |
| FreeBSD  | `ps -axo rss= -o comm=` | Two separate `-o` flags, not one comma-joined `-o comm=,rss=` – FreeBSD's `ps` treats the latter as a single keyword whose header text absorbs everything after the first `=`, producing one unlabelled column instead of two. RSS is reported in the same kB units as Linux's VmRSS. |

Both paths apply identical grouping and minimum-size filtering, so the picture looks the same regardless of platform.

## Tuning

Near the top of the file:

```python
MIN_MEMORY_MB = 1.0   # processes smaller than this are dropped
```

And the grouping rules:

```python
APP_GROUPS = [
    (re.compile(r"^firefox"), "firefox"),
    (re.compile(r"^(chrome|chromium|google-chrome)", re.IGNORECASE), "chrome"),
    ...
]
```

Add a line here to group any other multi-process app you want combined.

## Notes

Some processes owned by other users may be unreadable without elevated privileges – those are silently skipped rather than causing an error, on both platforms.

A single implausible reading (over 64 GB for one process) is discarded rather than displayed, as a guard against a bad sample dominating the whole treemap.

If you want to target a BSD significantly different from FreeBSD (OpenBSD, NetBSD, etc.), start a fresh conversation or fork this script – their `ps` output formats and available keywords differ enough that they're best handled as their own case rather than bolted onto this one.

## Acknowledgements and inspiration

Users of FreeBSD who wish to avoid vibe coding might like to try [FreeBSD Memory Visualizer](https://github.com/johnrobdoyle/FreeBSD-Memory-Treemap) – [https://redd.it/1wgb5ew](https://www.reddit.com/r/freebsd/comments/1wgb5ew/freebsd_memory_visualizer/).

johnrobdoyle's work inspired me to try Claude for something similar on Kubuntu – this was my first intentional experiment with vibe-coding. After it was good enough on Linux, I took Claude a few steps further to make the app cross-platform.

If the app is inaccurate in some ways, I'm not upset. It's the type of app that I'll occasionally have on a peripheral display, to be seen at a glance (not visually dissected).

I'm prepared to share my conversation with Claude.
