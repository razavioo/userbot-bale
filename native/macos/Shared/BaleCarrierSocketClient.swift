import Foundation
import Darwin

final class BaleCarrierSocketClient {
    private let fileDescriptor: Int32

    init(socketURL: URL) throws {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else {
            throw POSIXError(.init(rawValue: errno))
        }
        self.fileDescriptor = fd

        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)

        let path = socketURL.path
        let maxLength = MemoryLayout.size(ofValue: address.sun_path)
        let bytes = Array(path.utf8)
        guard bytes.count < maxLength else {
            close(fd)
            throw NSError(domain: "BaleCarrierSocketClient", code: 1, userInfo: [NSLocalizedDescriptionKey: "socket path too long"])
        }

        withUnsafeMutableBytes(of: &address.sun_path) { rawBuffer in
            guard let rebound = rawBuffer.bindMemory(to: CChar.self).baseAddress else { return }
            for (index, byte) in bytes.enumerated() {
                rebound[index] = CChar(bitPattern: byte)
            }
            rebound[bytes.count] = 0
        }

        let len = socklen_t(MemoryLayout<sockaddr_un>.size)
        let result = withUnsafePointer(to: &address) { ptr -> Int32 in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, len)
            }
        }
        guard result == 0 else {
            let error = POSIXError(.init(rawValue: errno))
            close(fd)
            throw error
        }
    }

    deinit {
        close(fileDescriptor)
    }

    func sendPacket(_ data: Data) throws {
        try writeFrame(data)
    }

    func readPacket() throws -> Data? {
        let lengthBytes = try readExact(4)
        guard let header = lengthBytes, header.count == 4 else {
            return nil
        }
        let length = header.reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
        if length == 0 {
            return Data()
        }
        return try readExact(Int(length))
    }

    private func writeFrame(_ payload: Data) throws {
        var length = UInt32(payload.count).bigEndian
        let lengthData = withUnsafeBytes(of: &length) { Data($0) }
        try writeAll(lengthData)
        try writeAll(payload)
    }

    private func writeAll(_ data: Data) throws {
        try data.withUnsafeBytes { rawBuffer in
            guard let base = rawBuffer.bindMemory(to: UInt8.self).baseAddress else { return }
            var remaining = rawBuffer.count
            var offset = 0
            while remaining > 0 {
                let written = Darwin.write(fileDescriptor, base.advanced(by: offset), remaining)
                if written < 0 {
                    throw POSIXError(.init(rawValue: errno))
                }
                remaining -= written
                offset += written
            }
        }
    }

    private func readExact(_ count: Int) throws -> Data? {
        var buffer = Data(count: count)
        let readCount: Int = try buffer.withUnsafeMutableBytes { rawBuffer in
            guard let base = rawBuffer.bindMemory(to: UInt8.self).baseAddress else { return 0 }
            var offset = 0
            var remaining = count
            while remaining > 0 {
                let result = Darwin.read(fileDescriptor, base.advanced(by: offset), remaining)
                if result == 0 {
                    return offset
                }
                if result < 0 {
                    throw POSIXError(.init(rawValue: errno))
                }
                remaining -= result
                offset += result
            }
            return offset
        }
        guard readCount == count else {
            return nil
        }
        return buffer
    }
}
