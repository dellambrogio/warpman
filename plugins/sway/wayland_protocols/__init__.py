"""Generated pywayland bindings for protocols pywayland doesn't ship.

wlr_layer_shell_unstable_v1.py was generated from swaylock-plugin's
wlr-layer-shell-unstable-v1.xml with:

    python -m pywayland.scanner -i /usr/share/wayland/wayland.xml \\
        /usr/share/wayland-protocols/stable/xdg-shell/xdg-shell.xml \\
        wlr-layer-shell-unstable-v1.xml -o out/

keeping only the layer-shell module, with its relative `.wayland` / `.xdg_shell`
imports pointed at pywayland's built-in modules (so the proxy classes match the
ones pywayland's registry hands out).
"""
