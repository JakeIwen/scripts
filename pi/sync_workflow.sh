run_sync_workflow() {
  sync_preflight || return
  sync_non_python || return
  sync_python_packages || return
  sync_compute
}
