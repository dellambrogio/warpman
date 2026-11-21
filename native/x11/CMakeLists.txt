add_executable(i3lock-warpman main.c saver.c)
target_compile_options(i3lock-warpman PRIVATE ${WARPMAN_C_FLAGS})
target_compile_definitions(i3lock-warpman PRIVATE
    WARPMAN_VERSION="${PROJECT_VERSION}"
    WARPMAN_LOG_NAME="warpman-lock-x11.log")
target_link_libraries(i3lock-warpman PRIVATE warpman_core PkgConfig::X11)
install(TARGETS i3lock-warpman RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR} COMPONENT i3lock)
warpman_man(i3lock-warpman i3lock)
