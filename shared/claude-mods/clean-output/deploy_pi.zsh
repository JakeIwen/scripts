#!/bin/zsh
# Kept so `zsh deploy_pi.zsh` from this folder still works; the shared
# script beside the mods does the install.
exec zsh ${0:A:h}/../deploy_pi.zsh clean-output
