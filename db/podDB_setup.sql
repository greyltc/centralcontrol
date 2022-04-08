CREATE TYPE "device_type" AS ENUM (
  'solar cell',
  'LED'
);

CREATE TYPE "shape" AS ENUM (
  'circle',
  'rectangle'
);

CREATE TYPE "source" AS ENUM (
  'voltage',
  'current'
);

CREATE TYPE "user_type" AS ENUM (
  'superuser',
  'group_member',
  'visitor'
);

CREATE TYPE "tco_type" AS ENUM (
  'ITO',
  'FTO',
  'AZO'
);

CREATE TABLE "tbl_users" (
  "id" SERIAL PRIMARY KEY,
  "name" text,
  "created_at" timestampz,
  "type" user_type
);

CREATE TABLE "tbl_iv_run" (
  "id" SERIAL PRIMARY KEY,
  "user_id" integer,
  "start_date" timestampz,
  "UUID" uuid,
  "name" text,
  "suns" real,
  "recipe" text,
  "duration" interval,
  "placeholder" integer
);

CREATE TABLE "tbl_substrates" (
  "id" SERIAL PRIMARY KEY,
  "substrate_type_id" integer,
  "label" text UNIQUE,
  "label_creation" timestamp
);

CREATE TABLE "tbl_substrate_types" (
  "id" SERIAL PRIMARY KEY,
  "mfg" text,
  "batch" text,
  "tco_pattern_name" text,
  "tco_type" tco_type,
  "sheet_resistance" real,
  "t550" real
);

CREATE TABLE "tbl_layouts" (
  "id" SERIAL PRIMARY KEY,
  "name" text,
  "version" text,
  "substrate_extents" box
);

CREATE TABLE "tbl_layout_pixel" (
  "id" SERIAL PRIMARY KEY,
  "layout_id" integer,
  "pixel" integer,
  "light_circle" circle,
  "dark_circle" circle,
  "light_outline" path,
  "dark_outline" path
);

CREATE TABLE "tbl_devices" (
  "id" SERIAL PRIMARY KEY,
  "substrate_id" integer,
  "layout_pixel_id" integer,
  "type" device_type
);

CREATE TABLE "tbl_setups" (
  "id" SERIAL PRIMARY KEY,
  "name" text,
  "location" text
);

CREATE TABLE "tbl_measurement_slot" (
  "id" SERIAL PRIMARY KEY,
  "setup_id" integer,
  "designator" text,
  "center" point
);

CREATE TABLE "tbl_iv_event" (
  "id" SERIAL PRIMARY KEY,
  "device_id" integer,
  "run_id" integer,
  "start_record_id" integer,
  "smu_id" integer,
  "n_points" integer,
  "forward" bool,
  "duration" interval,
  "rate" real,
  "source" source,
  "metadata_placeholder" real
);

CREATE TABLE "tbl_ss_event" (
  "id" SERIAL PRIMARY KEY,
  "device_id" integer,
  "run_id" integer,
  "start_record_id" integer,
  "smu_id" integer,
  "n_points" integer,
  "duration" interval,
  "source_value" real,
  "source" source,
  "metadata_placeholder" real
);

CREATE TABLE "tbl_mppt_event" (
  "id" SERIAL PRIMARY KEY,
  "device_id" integer,
  "run_id" integer,
  "start_record_id" integer,
  "smu_id" integer,
  "n_points" integer,
  "duration" interval,
  "algorithm_name" text,
  "algorithm_version" text,
  "algorithm_parameters" jsonb,
  "source" source,
  "metadata_placeholder" real
);

CREATE TABLE "tbl_smu1_iv_dat" (
  "id" SERIAL PRIMARY KEY,
  "hardware_timestamp" real,
  "voltage" real,
  "current" real,
  "status" bit(32)
);

CREATE TABLE "tbl_smu1_ss_dat" (
  "id" SERIAL PRIMARY KEY,
  "hardware_timestamp" real,
  "voltage" real,
  "current" real,
  "status" bit(32)
);

CREATE TABLE "tbl_smu1_mppt_dat" (
  "id" SERIAL PRIMARY KEY,
  "hardware_timestamp" real,
  "voltage" real,
  "current" real,
  "status" bit(32)
);

CREATE TABLE "tbl_smus" (
  "id" SERIAL PRIMARY KEY,
  "name" text,
  "idn" text
);

CREATE TABLE "tbl_slot_substrate_mapping" (
  "id" SERIAL PRIMARY KEY,
  "slot_id" integer,
  "substrate_id" integer,
  "run_id" integer
);

CREATE TABLE "tbl_slot_smu_slot_mapping" (
  "id" SERIAL PRIMARY KEY,
  "smu_id" integer,
  "run_id" integer,
  "slot_id" integer
);

ALTER TABLE "tbl_iv_run" ADD FOREIGN KEY ("user_id") REFERENCES "tbl_users" ("id");

ALTER TABLE "tbl_substrates" ADD FOREIGN KEY ("substrate_type_id") REFERENCES "tbl_substrate_types" ("id");

ALTER TABLE "tbl_layout_pixel" ADD FOREIGN KEY ("layout_id") REFERENCES "tbl_layouts" ("id");

ALTER TABLE "tbl_devices" ADD FOREIGN KEY ("substrate_id") REFERENCES "tbl_substrates" ("id");

ALTER TABLE "tbl_devices" ADD FOREIGN KEY ("layout_pixel_id") REFERENCES "tbl_layout_pixel" ("id");

ALTER TABLE "tbl_measurement_slot" ADD FOREIGN KEY ("setup_id") REFERENCES "tbl_setups" ("id");

ALTER TABLE "tbl_iv_event" ADD FOREIGN KEY ("device_id") REFERENCES "tbl_devices" ("id");

ALTER TABLE "tbl_iv_event" ADD FOREIGN KEY ("run_id") REFERENCES "tbl_iv_run" ("id");

ALTER TABLE "tbl_iv_event" ADD FOREIGN KEY ("start_record_id") REFERENCES "tbl_smu1_iv_dat" ("id");

ALTER TABLE "tbl_iv_event" ADD FOREIGN KEY ("smu_id") REFERENCES "tbl_smus" ("id");

ALTER TABLE "tbl_ss_event" ADD FOREIGN KEY ("device_id") REFERENCES "tbl_devices" ("id");

ALTER TABLE "tbl_ss_event" ADD FOREIGN KEY ("run_id") REFERENCES "tbl_iv_run" ("id");

ALTER TABLE "tbl_ss_event" ADD FOREIGN KEY ("start_record_id") REFERENCES "tbl_smu1_ss_dat" ("id");

ALTER TABLE "tbl_ss_event" ADD FOREIGN KEY ("smu_id") REFERENCES "tbl_smus" ("id");

ALTER TABLE "tbl_mppt_event" ADD FOREIGN KEY ("device_id") REFERENCES "tbl_devices" ("id");

ALTER TABLE "tbl_mppt_event" ADD FOREIGN KEY ("run_id") REFERENCES "tbl_iv_run" ("id");

ALTER TABLE "tbl_mppt_event" ADD FOREIGN KEY ("start_record_id") REFERENCES "tbl_smu1_mppt_dat" ("id");

ALTER TABLE "tbl_mppt_event" ADD FOREIGN KEY ("smu_id") REFERENCES "tbl_smus" ("id");

ALTER TABLE "tbl_slot_substrate_mapping" ADD FOREIGN KEY ("slot_id") REFERENCES "tbl_measurement_slot" ("id");

ALTER TABLE "tbl_slot_substrate_mapping" ADD FOREIGN KEY ("substrate_id") REFERENCES "tbl_substrates" ("id");

ALTER TABLE "tbl_slot_substrate_mapping" ADD FOREIGN KEY ("run_id") REFERENCES "tbl_iv_run" ("id");

ALTER TABLE "tbl_slot_smu_slot_mapping" ADD FOREIGN KEY ("smu_id") REFERENCES "tbl_smus" ("id");

ALTER TABLE "tbl_slot_smu_slot_mapping" ADD FOREIGN KEY ("run_id") REFERENCES "tbl_iv_run" ("id");

ALTER TABLE "tbl_slot_smu_slot_mapping" ADD FOREIGN KEY ("slot_id") REFERENCES "tbl_measurement_slot" ("id");

COMMENT ON TABLE "tbl_users" IS 'One row per user';

COMMENT ON TABLE "tbl_iv_run" IS 'One row per time Start button pressed on setup';

COMMENT ON COLUMN "tbl_iv_run"."placeholder" IS 'TODO: many cols to add here';

COMMENT ON TABLE "tbl_substrates" IS 'One row per substrate labeled';

COMMENT ON TABLE "tbl_substrate_types" IS 'One per substrate type';

COMMENT ON COLUMN "tbl_substrate_types"."sheet_resistance" IS 'ohm/sq';

COMMENT ON COLUMN "tbl_substrate_types"."t550" IS 'percent transmission at 550nm';

COMMENT ON TABLE "tbl_layouts" IS 'One row per unique layout design';

COMMENT ON TABLE "tbl_layout_pixel" IS 'One row per pixel on a layout design';

COMMENT ON COLUMN "tbl_layout_pixel"."pixel" IS 'pixel number';

COMMENT ON COLUMN "tbl_layout_pixel"."light_circle" IS 'dims are mm, for circular illuminated area';

COMMENT ON COLUMN "tbl_layout_pixel"."dark_circle" IS 'dims are mm, for circular dark area';

COMMENT ON COLUMN "tbl_layout_pixel"."light_outline" IS 'dims are mm, illuminated outline area';

COMMENT ON COLUMN "tbl_layout_pixel"."dark_outline" IS 'dims are mm, dark outline area';

COMMENT ON TABLE "tbl_devices" IS 'One row per individual device fabricated, that is generally 6 per substrate';

COMMENT ON TABLE "tbl_setups" IS 'One row per measurement setup';

COMMENT ON TABLE "tbl_measurement_slot" IS 'One per measurement slot in a setup';

COMMENT ON TABLE "tbl_iv_event" IS 'One row per JV scan executed';

COMMENT ON COLUMN "tbl_iv_event"."source" IS 'TODO: check if this is redundant, redundant with status byte';

COMMENT ON COLUMN "tbl_iv_event"."metadata_placeholder" IS 'TODO: many cols to add here';

COMMENT ON TABLE "tbl_ss_event" IS 'One row per steady-state dwell executed';

COMMENT ON COLUMN "tbl_ss_event"."source" IS 'TODO: check if this is redundant, redundant with status byte';

COMMENT ON COLUMN "tbl_ss_event"."metadata_placeholder" IS 'TODO: many cols to add here';

COMMENT ON TABLE "tbl_mppt_event" IS 'One row per mppt interval executed';

COMMENT ON COLUMN "tbl_mppt_event"."source" IS 'TODO: check if this is redundant, redundant with status byte';

COMMENT ON COLUMN "tbl_mppt_event"."metadata_placeholder" IS 'TODO: many cols to add here';

COMMENT ON TABLE "tbl_smu1_iv_dat" IS 'One row per iv curve data point collected by SMU with ID=1';

COMMENT ON TABLE "tbl_smu1_ss_dat" IS 'One row per steady state data point collected by SMU with ID=1';

COMMENT ON TABLE "tbl_smu1_mppt_dat" IS 'One row per MPPT data point collected by SMU with ID=1';

COMMENT ON TABLE "tbl_smus" IS 'One row per smu';

COMMENT ON TABLE "tbl_slot_substrate_mapping" IS 'N rows per run where N is the number of slots loaded in that run';

COMMENT ON TABLE "tbl_slot_smu_slot_mapping" IS 'N rows per run where N is the number of slots loaded in that run';
