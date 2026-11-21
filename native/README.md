# native/: the lock screens in C

The C source of `swaylock-warpman` (sway) and `i3lock-warpman` (i3 / X11). How to use and set them up is in the [main README](../README.md#lock-screens); this file is about the code.

## Build, test, package

```sh
just native         # cmake -S native -B build && cmake --build build
just native-test    # ctest: parity, status, scene
just deb            # build/swaylock-warpman_*.deb, build/i3lock-warpman_*.deb
just preview        # the animation above your windows (sway), 10 s
just preview-x11    # the animation in a window (X11)
```

CMake options: `WARPMAN_WAYLAND`, `WARPMAN_X11`, `WARPMAN_TESTS` (all `ON`). The install prefix defaults to `/usr`, so the PAM file goes to `/etc/pam.d`.

## Layout

```
CMakeLists.txt        top level: flags, options, man pages, subdirectories
cmake/
  SwaylockPlugin.cmake  builds swaylock-plugin as a static library (see below)
  Packaging.cmake       CPack: one .deb per component (swaylock, i3lock)
core/                 shared by both lockers (static lib warpman_core)
  pyrandom.c            CPython's Mersenne Twister and random(), gauss(), randrange()…
  scene.c               lenses, line styles, grid (port of warpman/scene.py)
  drift.c               the animation (port of warpman/drift.py)
  status.c              CPU / battery text and on-battery detection (warpman/status.py)
  egl.c                 EGL display / surface / context through libepoxy
  gl_render.c           the shaders and renderer from warpman/gl.py, drawing to the screen
  opts.c                DRIFT_* variables and the shared command-line options, logging
wayland/              swaylock-warpman
  main.c                one binary, three modes (below)
  layer.c               the background: a wlr-layer-shell client, one surface per output
  pam_service.c         which /etc/pam.d service to authenticate with
  pam/swaylock-warpman  the PAM file the package installs (a conffile)
x11/                  i3lock-warpman
  main.c                one binary, three modes (below)
  saver.c               draws into xsecurelock's window ($XSCREENSAVER_WINDOW)
man/                  the two man pages (installed gzipped)
tests/                ctest; golden.txt comes from gen_golden.py (`just golden`)
third_party/swaylock-plugin   git submodule
```

## How the pieces fit

**swaylock-warpman** is one binary that runs in three modes:

- `swaylock-warpman [swaylock options] [-- drift options]` locks the screen. It calls swaylock-plugin's `main()` (renamed to `swaylock_plugin_main`) with `-f -c 000000 --command "<itself> --background <drift options>"`.
- `--background`: swaylock-plugin runs an embedded Wayland compositor and starts this command as a client of it (`WAYLAND_SOCKET`). The client puts a layer-shell surface on each output and draws the animation with EGL/GL. Its stderr goes to `$XDG_RUNTIME_DIR/warpman-lock.log`. When it fails, it waits 10 s before exiting, so swaylock-plugin's restarts aren't a tight loop.
- `--preview` runs the same client on the real compositor (overlay layer, 10 s).

**i3lock-warpman** is also one binary with three modes:

- With no mode flag, it locks the screen: it sets `XSECURELOCK_SAVER` to itself, passes the drift options in `WARPMAN_SAVER_ARGS` (separated by `\x1f`) and `exec`s xsecurelock.
- `--saver` is the saver mode. xsecurelock starts the binary once per monitor with `XSCREENSAVER_WINDOW` set, and that variable alone also selects this mode. The saver draws into that window, checks the window's size, visibility and DPMS once a second, and exits quietly on any X error, because the window can disappear at any moment. Its log goes to `$XDG_RUNTIME_DIR/warpman-lock-x11.log`.
- `--preview` opens a normal window with the animation.

xsecurelock does the locking and authentication, so a crash in the saver never unlocks the screen.

## swaylock-plugin, built in

`cmake/SwaylockPlugin.cmake` mirrors upstream's `meson.build`. It copies two files into the build directory with one edit each:

- `main.c`: `main` is renamed to `swaylock_plugin_main`.
- `pam.c`: `pam_start("swaylock-plugin"` becomes `pam_start(warpman_pam_service()`.

If either string is missing after a submodule bump, the configure step fails instead of silently building something different. `warpman_pam_service()` returns the first of `swaylock-warpman`, `swaylock-plugin` and `swaylock` found in `/etc/pam.d` or `/usr/lib/pam.d`, so the build tree also works before the package is installed. If none is found, `swaylock-warpman` refuses to lock, because without a PAM service the password could never be checked and you couldn't unlock.

To update upstream:

```sh
git -C native/third_party/swaylock-plugin fetch && git -C native/third_party/swaylock-plugin checkout <rev>
just native-test && just deb
```

## Matching the Python drift

The C drift is meant to give exactly the same numbers as `warpman/drift.py` for the same seed. That holds because of three things:

- `pyrandom.c` reproduces CPython's `random` exactly. That includes `init_by_array` seeding, `_randbelow` drawing bits even for `n == 1`, and `gauss` caching its second value.
- Random numbers are drawn in the same order as in the Python code.
- Everything is compiled with `-ffp-contract=off`, so the compiler never fuses a multiply and an add.

`tests/test_parity.c` compares 3,824 values from runs at seed 42 against `golden.txt`, and they must match bit for bit. After changing `warpman/scene.py` or `warpman/drift.py`, make the same change in C and regenerate the reference with `just golden`.

## Testing without locking yourself out

- **sway:** don't test a new build on your own session. Start a nested headless sway (`WLR_BACKENDS=headless sway -c <minimal config>`), set `WAYLAND_DISPLAY` to its socket, and run the lock there. `grim` takes screenshots of it.
- **X11:** use a separate X server, for example `Xwayland :37 -geometry 1920x1080` or `Xvfb`. To test lock mode without really locking, put a stand-in `xsecurelock` script earlier in `PATH`. It should print its arguments and environment, then run `$XSECURELOCK_SAVER` with `XSCREENSAVER_WINDOW` pointing at a window.
- The recovery binding in the main README (`pkill -x swaylock-warpman; $lock`) restarts a stuck lock screen while locked.

## Still to verify

swaylock-warpman is verified on a real sway session (lock, PAM unlock).

- [ ] i3lock-warpman on a real i3 session with real xsecurelock: lock, unlock, several monitors, `xss-lock`
- [ ] DPMS: the saver stops drawing while the monitor is off
- [ ] on-battery pause on a laptop (both lockers)
