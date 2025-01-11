import csv
import pathlib
import time
from centralcontrol.amsmu import AmSmu

TIMESTAMP = int(time.time())

# --- SMU CONFIG ---
SMU_HOST = "SMU-A"
SMU_PORTS = [50001, 50002, 50003, 50004]
#SMU_PORTS = [50004]

# instrument config
SMU_KWARGS = {"line_frequency": 50}

# create root folder for calibration data if neccessary
ROOT_CAL_FOLDER_PATH = pathlib.Path("data").joinpath("calibration")
if not ROOT_CAL_FOLDER_PATH.exists():
    ROOT_CAL_FOLDER_PATH.mkdir(parents=True)


def decompose_idn(idn_str: str) -> dict:
    manufacturer, model, ctrl_firmware, ch_serial, ch_firmware = idn_str.split(",")

    return {
        "manufacturer": manufacturer,
        "model": model,
        "ctrl_firmware": ctrl_firmware,
        "ch_serial": ch_serial,
        "ch_firmware": ch_firmware,
    }


for SMU_PORT in SMU_PORTS:
    SMU_ADDRESS = f"socket://{SMU_HOST}:{SMU_PORT}"

    with AmSmu(SMU_ADDRESS, **SMU_KWARGS) as smu:
        smu_idn = smu.idn
        serial = decompose_idn(smu_idn)["ch_serial"]
        print(f"Connected to: {smu_idn}")

        # create folder for smu board data
        CAL_FOLDER_PATH = ROOT_CAL_FOLDER_PATH.joinpath(f"{serial}")
        if not CAL_FOLDER_PATH.exists():
            CAL_FOLDER_PATH.mkdir(parents=True)

        # create backup calibration file
        file = CAL_FOLDER_PATH.joinpath(f"{TIMESTAMP}_{serial}_backup_cal.csv")
        open(file, "w").close()

        # get existing cal and write it to file
        old_cal_data = smu.query("cal:data?")
        with open(file, "a", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(old_cal_data.split(","))
        print(f"old calibration: {old_cal_data}")

        # overwrite calibration with new values
        # new_cal = [
        #     0.997128,
        #     0.003788,
        #     1.022679,
        #     0.000268,
        #     1.27323,
        #     0.005892,
        #     1.551865,
        #     0.061782,
        #     1.023141,
        #     0.000763,
        #     1.275301,
        #     0.005956,
        #     1.022791,
        #     0.000013,
        #     1.000000,
        #     -0.012141,
        #     1726825548,
        # ]
        # new_cal = [
        #     0.998550,
        #     -0.009777,
        #     1.023370,
        #     -0.002747,
        #     1.275341,
        #     0.006031,
        #     1.552762,
        #     0.061832,
        #     1.023370,
        #     -0.002747,
        #     1.275341,
        #     0.006031,
        #     1.023370,
        #     -0.002747,
        #     1.275341,
        #     0.006031,
        #     1729762868,
        # ]
        # new_cal = [
        #     0.998550,
        #     -0.009777,
        #     1.023370,
        #     -0.002747,
        #     1.275341,
        #     0.006031,
        #     1.552762,
        #     0.061832,
        #     1.023370,
        #     -0.002747,
        #     1.275341,
        #     0.006031,
        #     1.023370,
        #     -0.002747,
        #     1.275341,
        #     0.006031,
        #     1729762868,
        # ]
        new_cal = [
            0.998497,
            -0.009665,
            1.023318,
            -0.002838,
            1.275528,
            0.005940,
            1.551907,
            0.061800,
            1.023318,
            -0.002838,
            1.275528,
            0.005940,
            1.022955,
            -0.000161,
            1.275528,
            0.005940,
            1730902030,
        ]
        smu.write(f"cal:data {','.join([str(p) for p in new_cal])}")
        new_cal_data = smu.query("cal:data?")
        print(f"reset calibration: {new_cal_data}")
