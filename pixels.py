import pyautogui, keyboard, time, mouse

path = "scripts\Pixels\coords.txt"

while True:

    x, y = pyautogui.position()

    if keyboard.is_pressed("up"):
        y-=1

    if keyboard.is_pressed("down"):
        y+=1

    if keyboard.is_pressed("left"):
        x-=1

    if keyboard.is_pressed("right"):
        x+=1

    if mouse.is_pressed("right"):
        with open(path, "a") as file:
            file.write(f"x: {x}, y: {y}\n")

    pyautogui.moveTo(x, y)
    
    print(f"x: {x}, y: {y}", end="\r")
    time.sleep(0.01)
