-- Move the existing relation. No row, constraint or validation state is changed.
CREATE SCHEMA compute;
REVOKE ALL ON SCHEMA compute FROM PUBLIC;
CREATE TABLE compute.schema_version (version integer PRIMARY KEY, checksum text NOT NULL);
ALTER TABLE instance.compute_presets SET SCHEMA compute;
ALTER TABLE compute.compute_presets RENAME TO presets;
DO $$
DECLARE item record; target text;
BEGIN
 FOR item IN SELECT conname FROM pg_constraint WHERE conrelid='compute.presets'::regclass ORDER BY oid LOOP
  IF item.conname NOT LIKE 'compute_presets_%' THEN
   RAISE EXCEPTION 'unrecognized preset constraint name';
  END IF;
  target := regexp_replace(item.conname, '^compute_presets', 'presets');
  EXECUTE format('ALTER TABLE compute.presets RENAME CONSTRAINT %I TO %I', item.conname, target);
 END LOOP;
 -- Renaming a PK/UNIQUE constraint already renames its supporting index.
 FOR item IN SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
             WHERE i.indrelid='compute.presets'::regclass AND c.relname LIKE 'compute_presets_%' LOOP
  target := regexp_replace(item.relname, '^compute_presets', 'presets');
  EXECUTE format('ALTER INDEX compute.%I RENAME TO %I', item.relname, target);
 END LOOP;
END $$;
