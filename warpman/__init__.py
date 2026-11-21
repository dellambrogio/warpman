"""warpman - warped-grid wallpapers, an animated drift, and lock-screen backgrounds.

Modules:
    scene   lenses, the warp, grid lines, line styles; cairo drawing and saving
    drift   the animation (Sim): wandering lenses, scene changes, rainbow colouring
    gl      GPU renderer (moderngl), offscreen
    status  CPU / battery status text, on-battery detection
    sound   generative audio for the drift window
    record  MP4 recording through ffmpeg

The programs live outside the package: apps/ (still images, editor, drift window).
The lock screens are native programs in native/ (swaylock-warpman, i3lock-warpman).
"""
