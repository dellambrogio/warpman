# warpman — `just` lists recipes; extra args pass through, e.g. `just drift --seed 7`

py := ".venv/bin/python"

default:
    @just --list

# create the venv and install requirements
setup:
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt

# still PNG(s) into out/
still *args:
    {{py}} warpman.py {{args}}

# live editor
gui *args:
    {{py}} warpman_gui.py {{args}}

# animated drift in a window (GPU backend)
drift *args:
    {{py}} warpman_drift.py --backend gl {{args}}

# drift with generative sound
sound *args:
    {{py}} warpman_drift.py --backend gl --sound {{args}}

# fullscreen screensaver with sound
saver *args:
    {{py}} warpman_drift.py --backend gl --fullscreen --sound {{args}}

# rainbow drift (wave | ripple | cycle)
rainbow mode="wave" *args:
    {{py}} warpman_drift.py --backend gl --rainbow {{mode}} {{args}}

# lock the screen now with the drift background
lock:
    swaylock-plugin -f -c 000000 --command "{{justfile_directory()}}/lock/drift-lock-bg.sh"

# headless 15 s clip to out/clip.mp4
clip seed="42" secs="15" w="1280" h="800":
    mkdir -p out
    {{py}} warpman_drift.py --raw -W {{w}} -H {{h}} --fps 30 --seed {{seed}} | \
      ffmpeg -y -f rawvideo -pix_fmt bgr0 -s {{w}}x{{h}} -r 30 -i - -t {{secs}} \
             -c:v libx264 -crf 20 -pix_fmt yuv420p -movflags +faststart out/clip.mp4
