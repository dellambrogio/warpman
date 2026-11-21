# warpman — `just` lists recipes; extra args pass through, e.g. `just drift --seed 7`

py := ".venv/bin/python"

default:
    @just --list

# create the venv and install the package (editable) with all extras
setup:
    python3 -m venv .venv
    .venv/bin/pip install -e '.[gl,dev]'

# unit tests
test *args:
    {{py}} -m pytest {{args}}

# still PNG(s) into out/
still *args:
    {{py}} apps/still.py {{args}}

# live editor
gui *args:
    {{py}} apps/gui.py {{args}}

# animated drift in a window (GPU backend)
drift *args:
    {{py}} apps/drift.py --backend gl {{args}}

# drift with generative sound
sound *args:
    {{py}} apps/drift.py --backend gl --sound {{args}}

# fullscreen screensaver with sound
saver *args:
    {{py}} apps/drift.py --backend gl --fullscreen --sound {{args}}

# rainbow drift (wave | ripple | cycle)
rainbow mode="wave" *args:
    {{py}} apps/drift.py --backend gl --rainbow {{mode}} {{args}}

# build the native lock screens (swaylock-warpman, i3lock-warpman) into build/
native *args:
    git submodule update --init native/third_party/swaylock-plugin
    cmake -S native -B build -DCMAKE_BUILD_TYPE=Release {{args}}
    cmake --build build -j

# native unit tests (incl. bit-exact parity with the Python drift at seed 42)
native-test: native
    ctest --test-dir build --output-on-failure

# Debian packages: build/swaylock-warpman_*.deb and build/i3lock-warpman_*.deb
deb: native
    cd build && cpack

# regenerate the parity reference data after changing the Python drift / scene
golden:
    PYTHONPATH=. {{py}} native/tests/gen_golden.py > native/tests/golden.txt

# lock the screen now (sway), from the build tree
lock *args:
    build/wayland/swaylock-warpman {{args}}

# lock an X11 session (i3) now, from the build tree (needs xsecurelock)
lock-x11 *args:
    build/x11/i3lock-warpman {{args}}

# the lock-screen animation for a few seconds: above your windows (sway) ...
preview *args:
    build/wayland/swaylock-warpman --preview {{args}}

# ... or in an X11 window
preview-x11 *args:
    build/x11/i3lock-warpman --preview {{args}}

# headless 15 s clip to out/clip.mp4
clip seed="42" secs="15" w="1280" h="800":
    mkdir -p out
    {{py}} apps/drift.py --raw -W {{w}} -H {{h}} --fps 30 --seed {{seed}} | \
      ffmpeg -y -f rawvideo -pix_fmt bgr0 -s {{w}}x{{h}} -r 30 -i - -t {{secs}} \
             -c:v libx264 -crf 20 -pix_fmt yuv420p -movflags +faststart out/clip.mp4
