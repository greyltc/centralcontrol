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
  "name" varchar,
  "created_at" timestamp,
  "type" user_type
);

CREATE TABLE "tbl_iv_run" (
  "id" SERIAL PRIMARY KEY,
  "user_id" int,
  "start_date" datetime,
  "UUID" uint128,
  "name" varchar,
  "suns" float,
  "recipe" varchar,
  "duration" int,
  "placeholder" float
);

CREATE TABLE "tbl_substrates" (
  "id" SERIAL PRIMARY KEY,
  "substrate_type_id" int,
  "label" varchar UNIQUE,
  "label_creation" timestamp
);

CREATE TABLE "tbl_substrate_types" (
  "id" SERIAL PRIMARY KEY,
  "mfg" varchar,
  "batch" varchar,
  "tco_pattern_name" varchar,
  "tco_type" tco_type,
  "sheet_resistance" float,
  "t550" float
);

CREATE TABLE "tbl_layouts" (
  "id" SERIAL PRIMARY KEY,
  "name" varchar,
  "version" varchar
);

CREATE TABLE "tbl_layout_pixel" (
  "id" SERIAL PRIMARY KEY,
  "layout_id" int,
  "pixel" int,
  "area" float,
  "shape" shape,
  "x_dim" float,
  "x" float,
  "y" float
);

CREATE TABLE "tbl_devices" (
  "id" SERIAL PRIMARY KEY,
  "substrate_id" int,
  "layout_pixel_id" int,
  "type" device_type
);

CREATE TABLE "tbl_setups" (
  "id" SERIAL PRIMARY KEY,
  "name" varchar,
  "location" varchar
);

CREATE TABLE "tbl_measurement_slot" (
  "id" SERIAL PRIMARY KEY,
  "setup_id" int,
  "designator" varchar
);

CREATE TABLE "tbl_iv_event" (
  "id" SERIAL PRIMARY KEY,
  "device_id" int,
  "run_id" int,
  "start_sample_id" bigint,
  "n_points" int,
  "forward" bool,
  "rate" float,
  "source" source,
  "metadata_placeholder" float
);

CREATE TABLE "tbl_raw_smu_data" (
  "id" BIGSERIAL PRIMARY KEY,
  "smu_id" int,
  "hardware_timestamp" float,
  "voltage" float,
  "current" float,
  "status" uint32
);

CREATE TABLE "tbl_smus" (
  "id" SERIAL PRIMARY KEY,
  "name" varchar,
  "idn" varchar
);

CREATE TABLE "tbl_slot_substrate_mapping" (
  "id" SERIAL PRIMARY KEY,
  "slot_id" int,
  "substrate_id" int,
  "run_id" int
);

CREATE TABLE "tbl_slot_smu_slot_mapping" (
  "id" SERIAL PRIMARY KEY,
  "smu_id" int,
  "run_id" int,
  "slot_id" int
);

ALTER TABLE "tbl_iv_run" ADD FOREIGN KEY ("user_id") REFERENCES "tbl_users" ("id");

ALTER TABLE "tbl_substrates" ADD FOREIGN KEY ("substrate_type_id") REFERENCES "tbl_substrate_types" ("id");

ALTER TABLE "tbl_layout_pixel" ADD FOREIGN KEY ("layout_id") REFERENCES "tbl_layouts" ("id");

ALTER TABLE "tbl_devices" ADD FOREIGN KEY ("substrate_id") REFERENCES "tbl_substrates" ("id");

ALTER TABLE "tbl_devices" ADD FOREIGN KEY ("layout_pixel_id") REFERENCES "tbl_layout_pixel" ("id");

ALTER TABLE "tbl_measurement_slot" ADD FOREIGN KEY ("setup_id") REFERENCES "tbl_setups" ("id");

ALTER TABLE "tbl_iv_event" ADD FOREIGN KEY ("device_id") REFERENCES "tbl_devices" ("id");

ALTER TABLE "tbl_iv_event" ADD FOREIGN KEY ("run_id") REFERENCES "tbl_iv_run" ("id");

ALTER TABLE "tbl_iv_event" ADD FOREIGN KEY ("start_sample_id") REFERENCES "tbl_raw_smu_data" ("id");

ALTER TABLE "tbl_raw_smu_data" ADD FOREIGN KEY ("smu_id") REFERENCES "tbl_smus" ("id");

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

COMMENT ON COLUMN "tbl_layout_pixel"."area" IS 'cm^2';

COMMENT ON COLUMN "tbl_layout_pixel"."x_dim" IS 'null for square or circle shape, >0 for rectangle';

COMMENT ON TABLE "tbl_devices" IS 'One row per individual device fabricated, that is generally 6 per substrate';

COMMENT ON TABLE "tbl_setups" IS 'One row per measurement setup';

COMMENT ON TABLE "tbl_measurement_slot" IS 'One per measurement slot in a setup';

COMMENT ON TABLE "tbl_iv_event" IS 'One row per JV scan executed';

COMMENT ON COLUMN "tbl_iv_event"."source" IS 'TODO: check if this is redundant, redundant with status byte';

COMMENT ON COLUMN "tbl_iv_event"."metadata_placeholder" IS 'TODO: many cols to add here';

COMMENT ON TABLE "tbl_raw_smu_data" IS 'One row per data point collected by any SMU (this will be huge)';

COMMENT ON TABLE "tbl_smus" IS 'One row per smu';

COMMENT ON TABLE "tbl_slot_substrate_mapping" IS 'N rows per run where N is the number of slots loaded in that run';

COMMENT ON TABLE "tbl_slot_smu_slot_mapping" IS 'N rows per run where N is the number of slots loaded in that run';
