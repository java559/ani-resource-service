-- Execute only with all new/old readers stopped and the matching old binaries ready.
DO $$
DECLARE item record; target text;
BEGIN
 FOR item IN SELECT conname FROM pg_constraint WHERE conrelid='compute.presets'::regclass ORDER BY oid LOOP
  IF item.conname NOT LIKE 'presets_%' THEN
   RAISE EXCEPTION 'unrecognized preset constraint name';
  END IF;
  target := regexp_replace(item.conname, '^presets', 'compute_presets');
  EXECUTE format('ALTER TABLE compute.presets RENAME CONSTRAINT %I TO %I', item.conname, target);
 END LOOP;
 FOR item IN SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
             WHERE i.indrelid='compute.presets'::regclass AND c.relname LIKE 'presets_%' LOOP
  target := regexp_replace(item.relname, '^presets', 'compute_presets');
  EXECUTE format('ALTER INDEX compute.%I RENAME TO %I', item.relname, target);
 END LOOP;
END $$;
ALTER TABLE compute.presets RENAME TO compute_presets;
ALTER TABLE compute.compute_presets SET SCHEMA instance;
DROP TABLE compute.schema_version;
-- No CASCADE: unexpected objects must roll the whole recovery transaction back.
DROP SCHEMA compute;
