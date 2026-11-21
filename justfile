# warpman — `just` lists recipes; extra args pass through, e.g. `just drift --seed 7`

py := ".venv/bin/python"

default:
    @just --list

# create the venv and install the package (editable) with all extras
setup:
    python3 -m venv .venv
    .venv/bin/pip install -e '.[gl,wayland,dev]'

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

# lock the screen now with the drift background
lock:
    swaylock-plugin -f -c 000000 --command "{{justfile_directory()}}/plugins/sway/lock-bg.sh"

# lock an X11 session (i3) now with xsecurelock and the drift background
lock-x11:
    "{{justfile_directory()}}/plugins/i3/lock.sh"

# drift in an X11 window (what the X11 lock screen shows)
x11 *args:
    {{py}} plugins/i3/saver.py {{args}}

# headless 15 s clip to out/clip.mp4
clip seed="42" secs="15" w="1280" h="800":
    mkdir -p out
    {{py}} apps/drift.py --raw -W {{w}} -H {{h}} --fps 30 --seed {{seed}} | \
      ffmpeg -y -f rawvideo -pix_fmt bgr0 -s {{w}}x{{h}} -r 30 -i - -t {{secs}} \
             -c:v libx264 -crf 20 -pix_fmt yuv420p -movflags +faststart out/clip.mp4
