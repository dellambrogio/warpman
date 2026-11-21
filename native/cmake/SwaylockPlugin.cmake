# swaylock-plugin (https://github.com/mstoeckl/swaylock-plugin), built as a static library
# from the submodule in third_party/ and linked into swaylock-warpman.
#
# This mirrors upstream's meson.build. Two things differ:
#   - main() is renamed to swaylock_plugin_main(), so our main() can dispatch between the
#     locker and the background client (both are this one binary);
#   - the PAM service is "swaylock-warpman" (we ship /etc/pam.d/swaylock-warpman), with
#     a fallback for running from the build tree (see wayland/pam_service.c).
# Both edits are applied to copies in the build directory; the submodule stays untouched.
# swaylock-sleep-watcher (only used by --grace, needs logind) is not built.

set(SWL_DIR ${CMAKE_CURRENT_SOURCE_DIR}/third_party/swaylock-plugin)
if(NOT EXISTS ${SWL_DIR}/main.c)
    message(FATAL_ERROR "swaylock-plugin source missing: run `git submodule update --init`")
endif()

pkg_check_modules(WAYLAND_SERVER REQUIRED IMPORTED_TARGET wayland-server>=1.20)
pkg_check_modules(WAYLAND_PROTOCOLS REQUIRED wayland-protocols>=1.47)
pkg_check_modules(XKBCOMMON REQUIRED IMPORTED_TARGET xkbcommon)
pkg_get_variable(WL_PROTOCOL_DIR wayland-protocols pkgdatadir)
pkg_get_variable(WAYLAND_SCANNER_PATH wayland-scanner wayland_scanner)
find_program(WAYLAND_SCANNER NAMES wayland-scanner HINTS ${WAYLAND_SCANNER_PATH} REQUIRED)
find_library(PAM_LIBRARY pam REQUIRED)
find_path(PAM_INCLUDE_DIR security/pam_appl.h REQUIRED)

set(SWL_GEN ${CMAKE_CURRENT_BINARY_DIR}/swaylock-plugin)
file(MAKE_DIRECTORY ${SWL_GEN})

# --- protocols ------------------------------------------------------------------------
# wayland_protocol(<xml> CODE CLIENT SERVER): generate the wanted parts into SWL_GEN,
# appending the outputs to SWL_PROTO_SRC.
set(SWL_PROTO_SRC)
function(wayland_protocol xml)
    cmake_parse_arguments(P "CODE;CLIENT;SERVER" "" "" ${ARGN})
    get_filename_component(base ${xml} NAME_WE)
    set(out)
    if(P_CODE)
        add_custom_command(OUTPUT ${SWL_GEN}/${base}-protocol.c
            COMMAND ${WAYLAND_SCANNER} private-code ${xml} ${SWL_GEN}/${base}-protocol.c
            DEPENDS ${xml} VERBATIM)
        list(APPEND out ${SWL_GEN}/${base}-protocol.c)
    endif()
    if(P_CLIENT)
        add_custom_command(OUTPUT ${SWL_GEN}/${base}-client-protocol.h
            COMMAND ${WAYLAND_SCANNER} client-header ${xml} ${SWL_GEN}/${base}-client-protocol.h
            DEPENDS ${xml} VERBATIM)
        list(APPEND out ${SWL_GEN}/${base}-client-protocol.h)
    endif()
    if(P_SERVER)
        add_custom_command(OUTPUT ${SWL_GEN}/${base}-server-protocol.h
            COMMAND ${WAYLAND_SCANNER} server-header ${xml} ${SWL_GEN}/${base}-server-protocol.h
            DEPENDS ${xml} VERBATIM)
        list(APPEND out ${SWL_GEN}/${base}-server-protocol.h)
    endif()
    set(SWL_PROTO_SRC ${SWL_PROTO_SRC} ${out} PARENT_SCOPE)
endfunction()

set(proxy_protocols
    ${WL_PROTOCOL_DIR}/unstable/xdg-output/xdg-output-unstable-v1.xml
    ${WL_PROTOCOL_DIR}/unstable/linux-dmabuf/linux-dmabuf-unstable-v1.xml
    ${WL_PROTOCOL_DIR}/stable/viewporter/viewporter.xml
    ${WL_PROTOCOL_DIR}/staging/fractional-scale/fractional-scale-v1.xml
    ${WL_PROTOCOL_DIR}/staging/color-representation/color-representation-v1.xml
    ${WL_PROTOCOL_DIR}/staging/color-management/color-management-v1.xml
    ${SWL_DIR}/wayland-drm.xml)
foreach(xml ${proxy_protocols})
    wayland_protocol(${xml} CODE CLIENT SERVER)
endforeach()
wayland_protocol(${WL_PROTOCOL_DIR}/staging/ext-session-lock/ext-session-lock-v1.xml CODE CLIENT)
wayland_protocol(${WL_PROTOCOL_DIR}/stable/xdg-shell/xdg-shell.xml CODE SERVER)
# wlr-layer-shell: swaylock-plugin serves it, our background client uses it. The
# interface code is generated once and shared; both headers are generated.
wayland_protocol(${SWL_DIR}/wlr-layer-shell-unstable-v1.xml CODE CLIENT SERVER)

# --- config.h and the two edited sources ----------------------------------------------
set(SWL_VERSION "1.8.7")
file(WRITE ${SWL_GEN}/config.h.tmp
    "#pragma once\n"
    "#define SYSCONFDIR \"${CMAKE_INSTALL_FULL_SYSCONFDIR}\"\n"
    "#define SWAYLOCK_VERSION \"${SWL_VERSION} (swaylock-warpman ${PROJECT_VERSION})\"\n"
    "#define HAVE_SYSTEMD 0\n"
    "#define HAVE_ELOGIND 0\n")
configure_file(${SWL_GEN}/config.h.tmp ${SWL_GEN}/config.h COPYONLY)

# replace_checked(<in> <out> <from> <to>): copy with one edit; fail if <from> is missing,
# so a submodule bump can't silently drop it.
function(replace_checked in out from to)
    file(READ ${in} text)
    string(FIND "${text}" "${from}" at)
    if(at EQUAL -1)
        message(FATAL_ERROR "swaylock-plugin changed: '${from}' not found in ${in}")
    endif()
    string(REPLACE "${from}" "${to}" text "${text}")
    file(WRITE ${out}.tmp "${text}")
    configure_file(${out}.tmp ${out} COPYONLY)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${in})
endfunction()

# PAM service: chosen at run time by warpman_pam_service() (wayland/pam_service.c):
# swaylock-warpman when installed, else an existing swaylock-plugin / swaylock service.
replace_checked(${SWL_DIR}/pam.c ${SWL_GEN}/pam.c
    "pam_start(\"swaylock-plugin\"" "pam_start(warpman_pam_service()")
file(READ ${SWL_GEN}/pam.c pam_text)
string(PREPEND pam_text "const char *warpman_pam_service(void);\n")
string(REPLACE "/etc/pam.d/swaylock-plugin" "/etc/pam.d/swaylock-warpman" pam_text "${pam_text}")
file(WRITE ${SWL_GEN}/pam.c.tmp "${pam_text}")
configure_file(${SWL_GEN}/pam.c.tmp ${SWL_GEN}/pam.c COPYONLY)
replace_checked(${SWL_DIR}/main.c ${SWL_GEN}/main.c
    "int main(int argc, char **argv) {" "int swaylock_plugin_main(int argc, char **argv) {")

# --- the library ----------------------------------------------------------------------
add_library(swaylock_plugin STATIC
    ${SWL_DIR}/background-image.c ${SWL_DIR}/cairo.c ${SWL_DIR}/comm.c
    ${SWL_DIR}/forward-client.c ${SWL_DIR}/forward.c ${SWL_DIR}/log.c ${SWL_DIR}/loop.c
    ${SWL_GEN}/main.c ${SWL_DIR}/password.c ${SWL_DIR}/password-buffer.c
    ${SWL_DIR}/pool-buffer.c ${SWL_DIR}/render.c ${SWL_DIR}/seat.c ${SWL_DIR}/setsid.c
    ${SWL_DIR}/unicode.c ${SWL_GEN}/pam.c
    ${SWL_PROTO_SRC})
target_include_directories(swaylock_plugin PRIVATE ${SWL_DIR}/include ${SWL_GEN} ${PAM_INCLUDE_DIR})
target_compile_definitions(swaylock_plugin PRIVATE _POSIX_C_SOURCE=200809L)
target_compile_options(swaylock_plugin PRIVATE -w)   # upstream's code, upstream's warnings
target_link_libraries(swaylock_plugin PUBLIC
    PkgConfig::WAYLAND_CLIENT PkgConfig::WAYLAND_SERVER PkgConfig::XKBCOMMON
    PkgConfig::CAIRO ${PAM_LIBRARY} m rt)
# our client only needs the layer-shell client header (not swaylock's include dir, whose
# cairo.h would shadow the system one)
add_library(wlr_layer_shell_headers INTERFACE)
target_include_directories(wlr_layer_shell_headers INTERFACE ${SWL_GEN}/include-client)
file(MAKE_DIRECTORY ${SWL_GEN}/include-client)
add_custom_command(OUTPUT ${SWL_GEN}/include-client/wlr-layer-shell-unstable-v1-client-protocol.h
    COMMAND ${CMAKE_COMMAND} -E copy ${SWL_GEN}/wlr-layer-shell-unstable-v1-client-protocol.h
            ${SWL_GEN}/include-client/
    DEPENDS ${SWL_GEN}/wlr-layer-shell-unstable-v1-client-protocol.h)
add_custom_target(wlr_layer_shell_header DEPENDS
    ${SWL_GEN}/include-client/wlr-layer-shell-unstable-v1-client-protocol.h)
add_dependencies(wlr_layer_shell_headers wlr_layer_shell_header)

install(FILES ${SWL_DIR}/LICENSE DESTINATION ${CMAKE_INSTALL_DOCDIR}/../swaylock-warpman
        RENAME LICENSE.swaylock-plugin COMPONENT swaylock)
