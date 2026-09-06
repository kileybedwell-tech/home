import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

// Build an eBay lot photo: crop each source photo down to the item it
// contains, then lay the items out on a white ground the way they would sit
// on a table. Driven by `python -m ebay lot-photo`.
//
// usage: lot_photo <out.jpg> <cols> <maxPixels> <in1> <in2> ...

/// ImageIO applies the EXIF orientation itself when asked for a transformed
/// thumbnail, which is more trustworthy than hand-rolling the eight cases.
func loadImage(_ path: String) -> CGImage? {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil)
    else { return nil }
    let opts: [CFString: Any] = [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceCreateThumbnailWithTransform: true,
        kCGImageSourceThumbnailMaxPixelSize: 4032,
    ]
    if let img = CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary) {
        return img
    }
    return CGImageSourceCreateImageAtIndex(src, 0, nil)
}

/// Pixel bounds of the non-background subject, found by looking for rows and
/// columns with enough dark pixels. The shooting surface is near-white, so
/// anything meaningfully darker is the jewel case.
func subjectBounds(_ img: CGImage) -> CGRect {
    let w = img.width, h = img.height
    let bpr = w * 4
    var buf = [UInt8](repeating: 0, count: bpr * h)
    guard let ctx = CGContext(data: &buf, width: w, height: h, bitsPerComponent: 8,
                              bytesPerRow: bpr, space: CGColorSpaceCreateDeviceRGB(),
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    else { return CGRect(x: 0, y: 0, width: w, height: h) }
    ctx.draw(img, in: CGRect(x: 0, y: 0, width: w, height: h))

    let threshold = 185.0     // luminance below this counts as "subject"
    let minFraction = 0.03    // ignore stray specks and shadows

    var rowDark = [Int](repeating: 0, count: h)
    var colDark = [Int](repeating: 0, count: w)
    for y in 0..<h {
        let row = y * bpr
        for x in 0..<w {
            let i = row + x * 4
            let lum = 0.299 * Double(buf[i]) + 0.587 * Double(buf[i+1]) + 0.114 * Double(buf[i+2])
            if lum < threshold { rowDark[y] += 1; colDark[x] += 1 }
        }
    }
    func span(_ counts: [Int], _ total: Int) -> (Int, Int) {
        let need = Int(Double(total) * minFraction)
        var lo = 0, hi = counts.count - 1
        while lo < hi && counts[lo] < need { lo += 1 }
        while hi > lo && counts[hi] < need { hi -= 1 }
        return (lo, hi)
    }
    let (y0, y1) = span(rowDark, w)
    let (x0, x1) = span(colDark, h)
    if x1 <= x0 || y1 <= y0 { return CGRect(x: 0, y: 0, width: w, height: h) }

    // A little air around the case so the crop doesn't shave its edges.
    let padX = Double(x1 - x0) * 0.02, padY = Double(y1 - y0) * 0.02
    let rx0 = max(0.0, Double(x0) - padX), ry0 = max(0.0, Double(y0) - padY)
    let rx1 = min(Double(w), Double(x1) + padX), ry1 = min(Double(h), Double(y1) + padY)
    // CoreGraphics origin is bottom-left; the scan ran top-down.
    return CGRect(x: rx0, y: Double(h) - ry1, width: rx1 - rx0, height: ry1 - ry0)
}

let args = CommandLine.arguments
guard args.count >= 5, let cols = Int(args[2]), let maxPixels = Int(args[3]) else {
    FileHandle.standardError.write(
        "usage: lot_photo <out.jpg> <cols> <maxPixels> <in1> <in2> ...\n".data(using: .utf8)!)
    exit(2)
}
let outPath = args[1]
let inputs = Array(args[4...])
let rows = Int(ceil(Double(inputs.count) / Double(cols)))

var tiles: [CGImage] = []
for path in inputs {
    guard let img = loadImage(path) else {
        FileHandle.standardError.write("could not read \(path)\n".data(using: .utf8)!)
        exit(1)
    }
    let b = subjectBounds(img)
    tiles.append(img.cropping(to: b) ?? img)
}

// Size every cell to the widest/tallest tile so nothing is distorted.
let cellW = tiles.map { $0.width }.max()!
let cellH = tiles.map { $0.height }.max()!
let gap = Int(Double(cellW) * 0.05)
let margin = Int(Double(cellW) * 0.07)
let canvasW = cols * cellW + (cols - 1) * gap + 2 * margin
let canvasH = rows * cellH + (rows - 1) * gap + 2 * margin

guard let ctx = CGContext(data: nil, width: canvasW, height: canvasH, bitsPerComponent: 8,
                          bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                          bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { exit(1) }
ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
ctx.fill(CGRect(x: 0, y: 0, width: canvasW, height: canvasH))
ctx.interpolationQuality = .high

for (i, tile) in tiles.enumerated() {
    let r = i / cols, c = i % cols
    // Centre the last row when it is short, so a 3+2 layout looks deliberate.
    let inRow = min(cols, tiles.count - r * cols)
    let rowWidth = inRow * cellW + (inRow - 1) * gap
    let xStart = (canvasW - rowWidth) / 2
    let scale = min(Double(cellW) / Double(tile.width), Double(cellH) / Double(tile.height))
    let dw = Double(tile.width) * scale, dh = Double(tile.height) * scale
    let cellX = Double(xStart + c * (cellW + gap))
    let cellY = Double(canvasH - margin - (r + 1) * cellH - r * gap)
    ctx.draw(tile, in: CGRect(x: cellX + (Double(cellW) - dw) / 2,
                              y: cellY + (Double(cellH) - dh) / 2,
                              width: dw, height: dh))
}

guard var out = ctx.makeImage() else { exit(1) }

// eBay wants roughly 1600px on the long side; the tiles are full camera
// resolution, so the montage is far bigger than that until it is scaled.
let longest = max(out.width, out.height)
if longest > maxPixels {
    let scale = Double(maxPixels) / Double(longest)
    let sw = Int(Double(out.width) * scale), sh = Int(Double(out.height) * scale)
    if let sctx = CGContext(data: nil, width: sw, height: sh, bitsPerComponent: 8,
                            bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) {
        sctx.interpolationQuality = .high
        sctx.draw(out, in: CGRect(x: 0, y: 0, width: sw, height: sh))
        if let scaled = sctx.makeImage() { out = scaled }
    }
}

guard let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: outPath) as CFURL,
                                                 UTType.jpeg.identifier as CFString, 1, nil)
else { exit(1) }
CGImageDestinationAddImage(dest, out, [kCGImageDestinationLossyCompressionQuality: 0.9] as CFDictionary)
CGImageDestinationFinalize(dest)
print("wrote \(outPath) (\(out.width)x\(out.height))")
