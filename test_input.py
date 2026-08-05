import sys
import select

def read_paste():
    lines = []
    print("Paste something:")
    lines.append(input())
    while True:
        rlist, _, _ = select.select([sys.stdin], [], [], 0.1)
        if rlist:
            lines.append(input())
        else:
            break
    print("GOT:", lines)

read_paste()
