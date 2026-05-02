package com.baleobala.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BaleVpnServiceHelpersTest {

    @Test fun ipv4LiteralAcceptsValid() {
        assertTrue(BaleVpnService.isIpv4Literal("1.1.1.1"))
        assertTrue(BaleVpnService.isIpv4Literal("0.0.0.0"))
        assertTrue(BaleVpnService.isIpv4Literal("255.255.255.255"))
        assertTrue(BaleVpnService.isIpv4Literal("185.51.200.2"))
    }

    @Test fun ipv4LiteralRejectsInvalid() {
        assertFalse(BaleVpnService.isIpv4Literal(""))
        assertFalse(BaleVpnService.isIpv4Literal("1.2.3"))
        assertFalse(BaleVpnService.isIpv4Literal("1.2.3.4.5"))
        assertFalse(BaleVpnService.isIpv4Literal("256.0.0.1"))
        assertFalse(BaleVpnService.isIpv4Literal("a.b.c.d"))
        assertFalse(BaleVpnService.isIpv4Literal("1..2.3"))
        assertFalse(BaleVpnService.isIpv4Literal("01.02.03.04xyz"))
        assertFalse(BaleVpnService.isIpv4Literal("1.2.3.-1"))
    }

    @Test fun fallbackDnsNonEmptyAndAllValid() {
        assertTrue(BaleVpnService.FALLBACK_DNS.isNotEmpty())
        for (ip in BaleVpnService.FALLBACK_DNS) {
            assertTrue("expected valid IPv4 literal: $ip", BaleVpnService.isIpv4Literal(ip))
        }
        assertEquals(BaleVpnService.FALLBACK_DNS.size, BaleVpnService.FALLBACK_DNS.distinct().size)
    }
}
