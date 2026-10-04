#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' \
  'pi/deploy_video_library.sh is retired and refuses to deploy.' \
  'Use: python3 pi/deploy_python.py --update --service video-library.service' \
  'For rollback, follow the saved pre-package-unit rollback procedure in' \
  'pi/docs/deployment.md.' >&2
exit 1
