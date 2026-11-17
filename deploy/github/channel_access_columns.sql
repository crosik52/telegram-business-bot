DO $channel_access_columns$
DECLARE
    target regclass := to_regclass('public.required_channels');
BEGIN
    IF NOT pg_try_advisory_xact_lock(614875432, 1031) THEN
        RAISE EXCEPTION 'Channel access schema update is already running';
    END IF;
    IF target IS NULL OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_class WHERE oid = target AND relkind = 'r'
    ) THEN
        RAISE EXCEPTION 'Required channel table is absent or unsupported';
    END IF;
    LOCK TABLE public.required_channels IN ACCESS EXCLUSIVE MODE;
    -- Validate both pre-applied columns before the first ALTER. Never evaluate
    -- stored default expressions or inspect channel rows.
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid = target AND attname = 'require_premium_boost' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = target AND a.attname = 'require_premium_boost' AND NOT a.attisdropped
          AND a.atttypid = 'pg_catalog.bool'::regtype AND a.atttypmod = -1
          AND a.attnotnull AND a.attgenerated = '' AND a.attidentity = ''
          AND pg_get_expr(d.adbin, d.adrelid) = 'false'
    ) THEN
        RAISE EXCEPTION 'Required channel boost column schema mismatch';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid = target AND attname = 'channel_join_url' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute a
        LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = target AND a.attname = 'channel_join_url' AND NOT a.attisdropped
          AND a.atttypid = 'pg_catalog.varchar'::regtype AND a.atttypmod = 516
          AND NOT a.attnotnull AND a.attgenerated = '' AND a.attidentity = '' AND d.oid IS NULL
    ) THEN
        RAISE EXCEPTION 'Required channel invitation column schema mismatch';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid = target AND attname = 'require_premium_boost' AND NOT attisdropped
    ) THEN
        ALTER TABLE public.required_channels
            ADD COLUMN require_premium_boost BOOLEAN NOT NULL DEFAULT false;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid = target AND attname = 'channel_join_url' AND NOT attisdropped
    ) THEN
        ALTER TABLE public.required_channels ADD COLUMN channel_join_url VARCHAR(512);
    END IF;
END $channel_access_columns$;
