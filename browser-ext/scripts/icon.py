# A2A 아이콘 — 주황→분홍 둥근 사각형 + 흰 고리 두 개(채팅 화면의 로고와 같은 느낌). 외부 이미지 없이 그린다.
import zlib, struct, math, sys
def png(n, path):
    rows = []
    r = n * 0.22
    for y in range(n):
        row = bytearray([0])
        for x in range(n):
            # 둥근 모서리 안쪽인지(안티에일리어싱 1px)
            cx = min(max(x + .5, r), n - r); cy = min(max(y + .5, r), n - r)
            d = math.hypot(x + .5 - cx, y + .5 - cy)
            a = max(0.0, min(1.0, r - d + .5))
            t = (x + y) / (2 * n)
            R, G, B = int(255 - 10 * t), int(122 - 60 * t), int(69 + 40 * t)
            # 고리 두 개
            ring = 0.0
            for ox in (-0.12, 0.12):
                dd = math.hypot((x + .5) / n - 0.5 - ox, (y + .5) / n - 0.5) * n
                ring = max(ring, max(0.0, 1 - abs(dd - n * 0.17) / (n * 0.045)))
            ring = min(1.0, ring * 1.6)
            R = int(R + (255 - R) * ring); G = int(G + (255 - G) * ring); B = int(B + (255 - B) * ring)
            row += bytes([R, G, B, int(255 * a)])
        rows.append(bytes(row))
    raw = b''.join(rows)
    def chunk(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', n, n, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')
    open(path, 'wb').write(data)
for n in (16, 32, 48, 128): png(n, f'{sys.argv[1]}/icon-{n}.png')
