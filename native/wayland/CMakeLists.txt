add_executable(swaylock-warpman main.c layer.c pam_service.c)
target_compile_options(swaylock-warpman PRIVATE ${WARPMAN_C_FLAGS})
target_compile_definitions(swaylock-warpman PRIVATE
    WARPMAN_VERSION="${PROJECT_VERSION}"
    WARPMAN_LOG_NAME="warpman-lock.log")
target_link_libraries(swaylock-warpman PRIVATE
    warpman_core swaylock_plugin wlr_layer_shell_headers
    PkgConfig::WAYLAND_CLIENT PkgConfig::WAYLAND_EGL)
install(TARGETS swaylock-warpman RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR} COMPONENT swaylock)
install(FILES ${CMAKE_CURRENT_SOURCE_DIR}/pam/swaylock-warpman
        DESTINATION ${CMAKE_INSTALL_FULL_SYSCONFDIR}/pam.d COMPONENT swaylock)
warpman_man(swaylock-warpman swaylock)
