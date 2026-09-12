-- One-time repair of the inspected sandbox setup failure, not a general retry.
-- POST /projects explicitly rejects missing notes with HTTP 400. Release
-- 867762f omitted notes. The recorded failure has no remote ID. The corrected
-- request still uses runStep's exact remote read-back and atomic write claim.
-- Preserve the original evidence in audit before resetting only that operation.
INSERT INTO audit (id, user_id, action, detail, created_at)
SELECT 'repair-rejected-project-d1233930-1850-40f3-90db-ea63b60b6469',
  'system:migration:0002', 'sandbox_setup_repair',
  json_object('operationId', id, 'priorState', state, 'priorEvidence', evidence,
    'reason', 'Documented HTTP 400 rejection for missing project notes; corrected request requires exact remote read-back before any write.'),
  strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM operations
WHERE id = 'd1233930-1850-40f3-90db-ea63b60b6469'
  AND publication_id = 'sandbox-setup/7265' AND name = 'project'
  AND state = 'unknown' AND remote_id IS NULL
  AND updated_at = '2026-09-12T17:22:20.489Z'
  AND evidence = 'SciSure returned HTTP 400. The operation is paused for reconciliation.';
--> statement-breakpoint
UPDATE operations SET state = 'pending',
  evidence = 'Reviewed HTTP 400 rejection; missing notes corrected. Original evidence preserved in audit by migration 0002.',
  updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
WHERE id = 'd1233930-1850-40f3-90db-ea63b60b6469'
  AND publication_id = 'sandbox-setup/7265' AND name = 'project'
  AND state = 'unknown' AND remote_id IS NULL
  AND updated_at = '2026-09-12T17:22:20.489Z'
  AND evidence = 'SciSure returned HTTP 400. The operation is paused for reconciliation.'
  AND EXISTS (SELECT 1 FROM audit WHERE id = 'repair-rejected-project-d1233930-1850-40f3-90db-ea63b60b6469');
