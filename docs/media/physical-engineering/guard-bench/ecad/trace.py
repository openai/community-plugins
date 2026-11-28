from pathlib import Path
import sys, pcbnew as p, json, math, heapq, collections

f = Path(sys.argv[1])
b = p.LoadBoard(str(f))
groups = {a.GetReference(): a for a in b.GetFootprints()}


def pair(q):
    return (q.x, q.y)


def xy(n):
    return [round(n[0] / 1e6, 5), round(n[1] / 1e6, 5)]


def make(signal, source, dest):
    graph = collections.defaultdict(dict)
    current = []
    vias = []
    for t in b.GetTracks():
        if t.GetNetname() != "/" + signal:
            continue
        if isinstance(t, p.PCB_VIA):
            x, y = pair(t.GetPosition())
            vias.append((x, y))
            continue
        a = pair(t.GetStart())
        d = pair(t.GetEnd())
        layer = t.GetLayerName()
        current.append((a, d, layer, t.GetWidth() / 1e6))

    def connect(a, d, kind, dist):
        graph[a][d] = (dist, kind)
        graph[d][a] = (dist, kind)

    for a, d, l, w in current:
        connect((*a, l), (*d, l), "track", math.dist(a, d) / 1e6)
    for x, y in vias:
        connect((x, y, "F.Cu"), (x, y, "B.Cu"), "via", 0)
    for ref, num in [source, dest]:
        pad = groups[ref].FindPadByNumber(str(num))
        center = pair(pad.GetPosition())
        key = (ref, str(num))
        for q in list(graph):
            if len(q) == 3 and q[2] == "F.Cu" and pad.HitTest(p.VECTOR2I(*q[:2])):
                connect(key, q, "pad", math.dist(center, q[:2]) / 1e6)
        if not graph[key]:
            raise ValueError((key, center, list(graph)))
    start = (source[0], str(source[1]))
    end = (dest[0], str(dest[1]))
    best = {start: 0}
    prev = {}
    heap = [(0, 0, start)]
    count = 0
    while heap:
        dist, _, q = heapq.heappop(heap)
        if q == end:
            break
        if dist > best[q]:
            continue
        for nxt, (delta, kind) in graph[q].items():
            val = dist + delta
            if val < best.get(nxt, float("inf")):
                best[nxt] = val
                prev[nxt] = (q, kind)
                count += 1
                heapq.heappush(heap, (val, count, nxt))
    q = end
    path = []
    while q != start:
        n, kind = prev[q]
        if kind == "track":
            path.append(
                dict(
                    layer=n[2],
                    start_mm=xy(n),
                    end_mm=xy(q),
                    length_mm=round(math.dist(n[:2], q[:2]) / 1e6, 5),
                )
            )
        if kind == "via":
            path.append(dict(via_mm=xy(n), from_layer=n[2], to_layer=q[2]))
        q = n
    path.reverse()
    return dict(
        net=signal,
        from_pad=list(source),
        to_pad=list(dest),
        from_center_kicad_mm=xy(
            pair(groups[source[0]].FindPadByNumber(str(source[1])).GetPosition())
        ),
        to_center_kicad_mm=xy(
            pair(groups[dest[0]].FindPadByNumber(str(dest[1])).GetPosition())
        ),
        path=path,
        length_mm=sum(i.get("length_mm", 0) for i in path),
        via_count=sum("via_mm" in i for i in path),
        layers=sorted({i["layer"] for i in path if "layer" in i}),
        widths_mm=sorted({r[3] for r in current}),
    )


paths = [
    make("I2C_SDA", ("J1", "3"), ("U1", "6")),
    make("I2C_SCL", ("J1", "4"), ("U1", "4")),
]
outline = b.GetBoardEdgesBoundingBox()
netcounts = collections.Counter(
    t.GetLayerName() for t in b.GetTracks() if not isinstance(t, p.PCB_VIA)
)
alltrack = [
    dict(
        net=t.GetNetname(),
        layer=t.GetLayerName(),
        start_mm=xy(pair(t.GetStart())),
        end_mm=xy(pair(t.GetEnd())),
        width_mm=t.GetWidth() / 1e6,
    )
    for t in b.GetTracks()
    if not isinstance(t, p.PCB_VIA)
]
drill = [
    {
        "ref": k,
        "center_design_mm": xy(pair(a.GetPosition())),
        "diameter_mm": next(iter(a.Pads())).GetDrillSize().x / 1e6,
    }
    for k, a in groups.items()
    if k.startswith("H")
]
j = dict(
    coordinates="KiCad top view absolute millimeters (center 100,100); +X right; +Y down.",
    routed_paths=paths,
    segments_by_layer=dict(netcounts),
    total_vias=sum(isinstance(t, p.PCB_VIA) for t in b.GetTracks()),
    all_tracks=alltrack,
    mounting_holes=sorted(drill, key=lambda a: a["ref"]),
)
Path(sys.argv[2]).write_text(json.dumps(j, indent=2) + "\n")
for pth in paths:
    print(pth["net"], pth["layers"], pth["length_mm"], pth["via_count"])
