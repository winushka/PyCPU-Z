"""Генерация icon.ico для PyCPU-Z без сторонних библиотек."""

import struct

SIZE = 32
BG = (27, 30, 35)
ACCENT = (76, 154, 255)
EDGE = (42, 58, 82)


def make_pixels() -> list[list[tuple[int, int, int]]]:
    pixels = [[BG for _ in range(SIZE)] for _ in range(SIZE)]

    def rect(x0, y0, x1, y1, color):
        for y in range(max(0, y0), min(SIZE, y1)):
            for x in range(max(0, x0), min(SIZE, x1)):
                pixels[y][x] = color

    for offset in (6, 12, 18, 24):
        rect(offset, 1, offset + 2, 6, ACCENT)
        rect(offset, 26, offset + 2, 31, ACCENT)
        rect(1, offset, 6, offset + 2, ACCENT)
        rect(26, offset, 31, offset + 2, ACCENT)

    rect(6, 6, 26, 26, EDGE)
    rect(8, 8, 24, 24, ACCENT)
    rect(11, 11, 21, 21, BG)
    return pixels


def bmp_bytes(pixels) -> bytes:
    header = struct.pack(
        "<IiiHHIIiiII", 40, SIZE, SIZE * 2, 1, 32, 0, 0, 0, 0, 0, 0
    )
    body = bytearray()
    for y in range(SIZE - 1, -1, -1):
        for x in range(SIZE):
            red, green, blue = pixels[y][x]
            body += bytes((blue, green, red, 255))
    mask = bytes(SIZE * SIZE // 8)
    return header + bytes(body) + mask


def main() -> None:
    image = bmp_bytes(make_pixels())
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack(
        "<BBBBHHII", SIZE, SIZE, 0, 0, 1, 32, len(image), 6 + 16
    )
    with open("icon.ico", "wb") as handle:
        handle.write(header + entry + image)
    print("icon.ico готов")


if __name__ == "__main__":
    main()
