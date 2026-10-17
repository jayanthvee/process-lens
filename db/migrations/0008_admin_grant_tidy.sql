-- PL-034: the admin panel sets `tenants.min_extension_version` through the API (ADM-001),
-- so the direct column grant goes: one write path, not two.
REVOKE UPDATE (min_extension_version) ON tenants FROM processlens_admin;
