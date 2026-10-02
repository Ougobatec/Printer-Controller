def relative_move(axis, distance, speed=300):

    return [
        "G91",
        f"G1 {axis}{distance:g} F{speed}",
        "G90"
    ]

def absolute_move(x=None, y=None, z=None, speed=300):

    command = "G1"

    if x is not None:
        command += f" X{x:g}"

    if y is not None:
        command += f" Y{y:g}"

    if z is not None:
        command += f" Z{z:g}"

    command += f" F{speed}"

    return [
        "G90",
        command
    ]

def home():

    return "G28"

def get_position():

    return "M114"

def firmware_info():

    return "M115"