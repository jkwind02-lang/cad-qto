(vl-load-com)

(defun qto:export-benchmark-dxf (/ output previous-filedia result)
  (setq output (getenv "QTO_BENCHMARK_DXF"))
  (if (not output)
    (progn (princ "\nQTO benchmark DXF path is missing.") (exit)))
  (setq previous-filedia (getvar "FILEDIA"))
  (setvar "FILEDIA" 0)
  ;; Core Console does not expose an ActiveDocument COM object reliably.
  ;; DXFOUT is the supported command-line path and writes a separate copy.
  (setq result (vl-catch-all-apply 'command (list "_.DXFOUT" output "16")))
  (setvar "FILEDIA" previous-filedia)
  (if (vl-catch-all-error-p result)
    (progn
      (princ (strcat "\nQTO DXF export failed: " (vl-catch-all-error-message result)))
      (exit))
    (princ (strcat "\nQTO DXF export complete: " output)))
  (princ))

(princ)
