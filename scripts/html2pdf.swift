// Render an HTML file to a paginated A4 PDF with the system WebKit (Safari's engine).
// Build:  swiftc -O scripts/html2pdf.swift -o ~/lebillet-data/bin/html2pdf
// Use:    html2pdf in.html out.pdf [margin-points]
//         The margin is applied to every page by the print system, which WebKit
//         honours more reliably than CSS @page margins.
import AppKit
import WebKit

final class Renderer: NSObject, WKNavigationDelegate {
    let web: WKWebView
    let window: NSWindow
    let output: URL
    let margin: CGFloat

    init(input: URL, output: URL, margin: CGFloat) {
        self.output = output
        self.margin = margin
        let config = WKWebViewConfiguration()
        config.preferences.shouldPrintBackgrounds = true                       // cover colours, table headers
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: 794, height: 1123), configuration: config)   // A4 at 96 dpi
        window = NSWindow(contentRect: web.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        super.init()
        window.contentView = web
        web.navigationDelegate = self
        web.loadFileURL(input, allowingReadAccessTo: input.deletingLastPathComponent())
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        // Give web fonts and layout a moment before printing.
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { self.printPDF() }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { fail(error) }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { fail(error) }

    func fail(_ error: Error) {
        FileHandle.standardError.write("load failed: \(error)\n".data(using: .utf8)!)
        exit(1)
    }

    func printPDF() {
        let info = NSPrintInfo()
        info.paperSize = NSSize(width: 595.28, height: 841.89)             // A4 in points
        info.topMargin = margin; info.bottomMargin = margin; info.leftMargin = 0; info.rightMargin = 0
        info.horizontalPagination = .fit
        info.verticalPagination = .automatic
        info.jobDisposition = .save
        info.dictionary()[NSPrintInfo.AttributeKey.jobSavingURL] = output
        let op = web.printOperation(with: info)
        op.showsPrintPanel = false
        op.showsProgressPanel = false
        op.view?.frame = web.bounds
        op.runModal(for: window, delegate: self, didRun: #selector(done(_:success:contextInfo:)), contextInfo: nil)
    }

    @objc func done(_ op: NSPrintOperation, success: Bool, contextInfo: UnsafeMutableRawPointer?) {
        exit(success ? 0 : 1)
    }
}

let args = CommandLine.arguments
guard args.count == 3 || args.count == 4 else {
    FileHandle.standardError.write("usage: html2pdf in.html out.pdf [margin-points]\n".data(using: .utf8)!)
    exit(2)
}
let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let renderer = Renderer(input: URL(fileURLWithPath: args[1]).standardizedFileURL,
                        output: URL(fileURLWithPath: args[2]).standardizedFileURL,
                        margin: CGFloat(Double(args.count == 4 ? args[3] : "0") ?? 0))
app.run()
