import AppKit
import Foundation
import Vision

struct OcrNode: Codable {
  let text: String
  let confidence: Float
  let x1: Int
  let y1: Int
  let x2: Int
  let y2: Int
}

func fail(_ message: String) -> Never {
  FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
  exit(1)
}

if CommandLine.arguments.count < 2 {
  fail("usage: swift ocr_image.swift image.png")
}

let imagePath = CommandLine.arguments[1]
let imageUrl = URL(fileURLWithPath: imagePath)
guard let image = NSImage(contentsOf: imageUrl) else {
  fail("failed to read image: \(imagePath)")
}

var rect = CGRect(origin: .zero, size: image.size)
guard let cgImage = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
  fail("failed to convert image: \(imagePath)")
}

let width = CGFloat(cgImage.width)
let height = CGFloat(cgImage.height)
var output: [OcrNode] = []

let request = VNRecognizeTextRequest { request, error in
  if let error = error {
    fail("ocr failed: \(error.localizedDescription)")
  }

  let observations = request.results as? [VNRecognizedTextObservation] ?? []
  for observation in observations {
    guard let candidate = observation.topCandidates(1).first else { continue }
    let box = observation.boundingBox
    let x1 = Int((box.minX * width).rounded())
    let y1 = Int(((1.0 - box.maxY) * height).rounded())
    let x2 = Int((box.maxX * width).rounded())
    let y2 = Int(((1.0 - box.minY) * height).rounded())
    output.append(OcrNode(
      text: candidate.string,
      confidence: candidate.confidence,
      x1: x1,
      y1: y1,
      x2: x2,
      y2: y2
    ))
  }
}

request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.automaticallyDetectsLanguage = true

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
try handler.perform([request])

let data = try JSONEncoder().encode(output)
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write("\n".data(using: .utf8)!)
