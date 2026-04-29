package com.baleobala.vpn

import com.baleobala.vpn.bale.BaleProtos
import com.baleobala.vpn.proto.ProtoCodec
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream

class ProtoCodecTest {

    @Test fun varintEncodeMatchesProto3() {
        val out = ByteArrayOutputStream()
        ProtoCodec.encVarint(out, 0)
        assertEquals(1, out.size())
        assertEquals(0x00.toByte(), out.toByteArray()[0])

        out.reset()
        ProtoCodec.encVarint(out, 150)
        // 150 -> 0x96 0x01 (proto3 example)
        assertEquals(2, out.size())
        assertEquals(0x96.toByte(), out.toByteArray()[0])
        assertEquals(0x01.toByte(), out.toByteArray()[1])
    }

    @Test fun lenDelimRoundTrip() {
        val out = ByteArrayOutputStream()
        ProtoCodec.encLenDelim(out, 5, "hi".toByteArray())
        val bytes = out.toByteArray()
        // tag = (5 << 3) | 2 = 0x2a
        assertEquals(0x2a.toByte(), bytes[0])
        assertEquals(0x02.toByte(), bytes[1])
        assertEquals('h'.code.toByte(), bytes[2])
        assertEquals('i'.code.toByte(), bytes[3])

        val walked = ProtoCodec.walk(bytes)
        assertEquals(1, walked.size)
        val v = walked[0].value
        assertTrue(v is ProtoCodec.FieldValue.LenDelim)
        assertEquals("hi", String((v as ProtoCodec.FieldValue.LenDelim).bytes))
    }

    @Test fun startPhoneAuthEncodingHasAllRequiredFields() {
        val bytes = BaleProtos.encodeStartPhoneAuth(
            phoneNumber = 989121234567L,
            deviceHash = "fake-uuid".toByteArray(),
            deviceTitle = "android",
        )
        val fields = ProtoCodec.walk(bytes).map { it.number }.toSet()
        assertTrue("missing phone_number", 1 in fields)
        assertTrue("missing app_id", 2 in fields)
        assertTrue("missing api_key", 3 in fields)
        assertTrue("missing device_hash", 4 in fields)
        assertTrue("missing device_title", 5 in fields)
    }

    @Test fun parseJwtFindsTokenInsideBlob() {
        val jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.bbb"
        val noise = byteArrayOf(0x01, 0x02, 0x03) + jwt.toByteArray() + byteArrayOf(0x05)
        val found = BaleProtos.parseJwt(noise)
        assertEquals(jwt, found)
    }
}
