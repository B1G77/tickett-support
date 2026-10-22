// Render every page of a PDF to PNG with the system PDFKit, for visual checks.
// Build:  swiftc -O scripts/pdf2png.swift -o ~/lebillet-data/bin/pdf2png
// Use:    pdf2png in.pdf out-dir [width-px]
import AppKit
import PDFKit

let args = CommandLine.arguments
guard args.count >= 3, let doc = PDFDocument(url: URL(fileURLWithPath: args[1])) else {
    FileHandle.standardError.write("usage: pdf2png in.pdf out-dir [width-px]\n".data(using: .utf8)!)
    exit(2)
}
let width = CGFloat(Double(args.count > 3 ? args[3] : "900") ?? 900)
for i in 0..<doc.pageCount {
    guard let page = doc.page(at: i) else { continue }
    let box = page.bounds(for: .mediaBox)
    let size = NSSize(width: width, height: box.height * width / box.width)
    let image = page.thumbnail(of: size, for: .mediaBox)
    guard let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
          let png = rep.representation(using: .png, properties: [:]) else { continue }
    try png.write(to: URL(fileURLWithPath: "\(args[2])/page-\(String(format: "%02d", i + 1)).png"))
}
print("\(doc.pageCount) pages")
