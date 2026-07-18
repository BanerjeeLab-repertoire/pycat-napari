## [1.6.102] - 2026-07-18
### Fixed — **Session load: the source image no longer OOMs, and the dialog closes on load (viewer-reported).**
Loading a session reported **"0 layers"** and the terminal showed `Unable to allocate 5.79 GiB for an
array with shape (1000, 1080, 1440) float32`. The source is a long time-series, and
`_load_source_image_into_viewer` was reading it **whole** with `tifffile.imread` because its lazy
opener (`open_image_auto`, frame-by-frame) was unreachable — it looked for `file_io` on
`data_instance.central_manager`, which the loaded `BaseDataClass` does not carry, so it silently fell
to the eager read and ran out of memory. That failure is what left the session with no layers.

`file_io` is now passed in by the caller (`load_session(..., central_manager=…)`), so the lazy opener
is used. The fallbacks are lazy too: a memory-mapped read (no full allocation) before, only as a last
resort, the eager read.

**The Load dialog now closes on load.** Clicking Load loaded but left the dialog open, so the user had
to click Cancel to dismiss it — reading as "did it even work?". Load now loads and closes (the toast
reports what happened); Cancel is the only way to dismiss without loading.
### Notes
- The lazy wiring is tested headlessly (given a `file_io`, the lazy opener is used and the eager read
  is never reached; without one, memmap precedes any full allocation). That the lazy layer displays
  and scrubs still needs a viewer.
- **Not fixed here — the larger restoration:** the session still does not reopen the analysis method or
  rebuild its plots/tables. The manifest does not record which method was active, and the VPT
  track-layer rebuild only fires when the VPT panel is already open. So the dataframes come back but
  the working view does not. That is a real feature (record the active method; reopen it; have it
  rebuild from the restored data), needs design + viewer verification, and is the next session-load
  piece.

