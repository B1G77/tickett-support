// Merge PDFs (optionally page ranges) and stamp a footer and page number on
// every page except the first - WebKit cannot repeat footers on flowing pages.
// Build:  swiftc -O scripts/pdfmerge.swift -o ~/lebillet-data/bin/pdfmerge
// Use:    pdfmerge out.pdf "Footer text" a.pdf:1-4 b.pdf a.pdf:5-5
import AppKit
import PDFKit

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write("\(msg)\n".data(using: .utf8)!)
    exit(1)
}

let args = CommandLine.arguments
guard args.count >= 4 else { fail("usage: pdfmerge out.pdf \"footer\" in.pdf[:from-to] ...") }
let merged = PDFDocument()
for spec in args[3...] {
    var path = spec
    var range: ClosedRange<Int>? = nil
    if let colon = spec.lastIndex(of: ":"), spec[spec.index(after: colon)...].contains("-") {
        path = String(spec[..<colon])
        let bounds = spec[spec.index(after: colon)...].split(separator: "-").compactMap { Int($0) }
        if bounds.count == 2 { range = bounds[0]...bounds[1] }
    }
    guard let doc = PDFDocument(url: URL(fileURLWithPath: path)) else { fail("cannot open \(path)") }
    for i in 0..<doc.pageCount where range.map({ $0.contains(i + 1) }) ?? true {
        if let page = doc.page(at: i) { merged.insert(page, at: merged.pageCount) }
    }
}

let muted = NSColor(red: 0.42, green: 0.45, blue: 0.50, alpha: 1)
let ink = NSColor(red: 0.06, green: 0.08, blue: 0.09, alpha: 1)
func stamp(_ page: PDFPage, _ text: String, _ rect: NSRect, _ align: NSTextAlignment, _ color: NSColor, _ bold: Bool) {
    let ann = PDFAnnotation(bounds: rect, forType: .freeText, withProperties: nil)
    ann.contents = text
    ann.font = bold ? NSFont.systemFont(ofSize: 6.8, weight: .semibold) : NSFont.systemFont(ofSize: 6.8)
    ann.fontColor = color
    ann.color = .clear
    ann.alignment = align
    let border = PDFBorder()
    border.lineWidth = 0
    ann.border = border
    page.addAnnotation(ann)
}
for i in 1..<merged.pageCount {
    guard let page = merged.page(at: i) else { continue }
    let box = page.bounds(for: .mediaBox)
    stamp(page, args[2], NSRect(x: 54, y: 20, width: 320, height: 18), .left, muted, false)
    stamp(page, String(format: "%02d", i + 1), NSRect(x: box.width - 114, y: 20, width: 60, height: 18), .right, ink, true)
}
guard merged.write(to: URL(fileURLWithPath: args[1])) else { fail("cannot write \(args[1])") }
print("\(merged.pageCount) pages")
