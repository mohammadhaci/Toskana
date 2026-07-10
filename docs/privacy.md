# Privacy & data protection

Toskana is built privacy-first: it counts **objects** (drinks, dishes) crossing a
counter line. It does not identify people, and video never leaves the site.

## What is stored

| Data | Content | Retention |
|---|---|---|
| Event metadata | category/menu item, direction, camera, line, timestamp, confidence, anchor point | kept (no personal data) |
| Event snapshots | one JPEG of the pass area per counting event (staff hands/body may be visible) | deleted after `snapshot_retention_days` (default 30) |
| Data gaps | outage/drift markers (timestamps + reason) | kept |
| Calibration references | one downscaled grayscale frame per camera (drift detection) | kept until re-calibrated |

**Not stored, by design:**

- no continuous video recording — the stream is analyzed in memory and discarded,
- **no face recognition, no person identification, no biometric processing** of any
  kind; people are not even a counted class,
- no cloud upload — database, snapshots and models stay on the site machine,
- no audio.

## Retention

`snapshot_retention_days` in `config.yaml` controls the audit-snapshot window. An
in-process job runs daily; operators can trigger it any time via
`POST /api/system/retention/run` (System page) or `toskana cleanup` (cron). Expired
snapshots are deleted from disk, their event rows keep only the non-personal
metadata. Orphaned image files are swept too. The pass is idempotent and logged.

## Legal basis and works-council involvement (AT/DE)

This section is guidance, not legal advice — involve your data-protection officer.

- The likely GDPR basis is **legitimate interest (Art. 6(1)(f))**: inventory/revenue
  control on the pass, with minimal snapshots as the audit trail. Document the
  balancing test.
- Because the camera covers a **workplace**, employee-representation rules apply:
  - **Austria**: a system objectively capable of monitoring employees requires a
    works agreement (Betriebsvereinbarung, § 96 ArbVG) or, without a works council,
    individual consent.
  - **Germany**: co-determination under § 87 (1) No. 6 BetrVG (technical equipment
    suitable for monitoring performance/behavior).
- Run a **DPIA** (Art. 35 GDPR) if your DPO deems it required; this document plus
  [architecture.md](architecture.md) provide the technical input: purpose, data
  categories, retention, access model, safeguards.
- Post transparent signage at the counter and add the system to your Art. 30
  processing register.
- Employees' data-subject rights (access, erasure) are servable: snapshots are
  time-indexed per camera and deletable ahead of the retention window.

## Training-data licensing policy

- **Prototyping only**: publicly available footage (e.g. YouTube) may be used for
  early experiments; `training/` manifests record every source URL and license, and
  such models are not shipped to production.
- **Production**: models are fine-tuned exclusively on the restaurant's own footage,
  recorded with documented consent of the works council/staff, and stay the
  restaurant's property.
- Every exported model carries its provenance manifest (sources, parameters, git
  SHA) — see [training.md](training.md).

## Data ownership

The restaurant owns its database, snapshots and fine-tuned models. Uninstalling the
system removes all data with it; export is available at any time (SQLite file +
CSV export in the dashboard).
