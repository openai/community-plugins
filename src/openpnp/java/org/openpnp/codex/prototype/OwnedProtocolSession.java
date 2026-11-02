/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex.prototype;

import com.google.gson.*;
import java.util.*;
import org.openpnp.model.Configuration;
import org.openpnp.machine.reference.driver.GcodeDriver;

/** Test-only bounded protocol client. No physical grant, retry, raw command or new endpoint API. */
public final class OwnedProtocolSession implements AutoCloseable {
    private final TypedGcodeProfile.Bundle bundle;
    private final Configuration config;
    private final JsonObject expected;
    private final Map<String,JsonObject> receipts=new LinkedHashMap<>();
    private boolean connected,fenced;
    public OwnedProtocolSession(TypedGcodeProfile.Bundle bundle){TypedGcodeProfile.requireFixedProtocol(bundle);this.bundle=bundle;this.config=Configuration.get();this.expected=TypedGcodeProfile.describe(bundle);check();}
    private void check(){bundle.endpoint().check();if(Configuration.get()!=config||config.getMachine()!=bundle.machine()||bundle.driver().getClass()!=GcodeDriver.class||!TypedGcodeProfile.describe(bundle).equals(expected))throw new IllegalStateException("PROTOTYPE_CONFIGURATION_CHANGED");}
    public synchronized void connectOnce()throws Exception {
        check();if(connected||fenced)throw new IllegalStateException("PROTOTYPE_SESSION_NOT_FRESH");
        try {bundle.driver().connect();bundle.driver().receiveResponses();connected=true;}
        catch(Exception failure){fenced=true;bundle.driver().disconnect();throw failure;}
    }
    public synchronized JsonObject identifyOnce(String requestId) {
        if(requestId==null||!requestId.matches("[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"))throw new IllegalArgumentException("INVALID_REQUEST_ID");
        if(receipts.containsKey(requestId))return new JsonParser().parse(receipts.get(requestId).toString()).getAsJsonObject();
        if(fenced||!connected)throw new IllegalStateException("PROTOTYPE_SESSION_FENCED");
        if(receipts.size()>=64)throw new IllegalStateException("PROTOTYPE_REQUEST_LIMIT");
        check();JsonObject result=new JsonObject();result.addProperty("request_id",requestId);result.addProperty("command_kind","identify");
        result.addProperty("authority","owned-loopback-protocol-test");result.addProperty("native_driver","GcodeDriver");result.addProperty("physical_qualification",false);result.addProperty("motion_completion_observed",false);result.addProperty("automatic_replay",false);
        try {
            bundle.driver().sendCommand("M115");
            // Native confirmation and error latches are separate. In particular,
            // an error followed by ok must not become a successful typed receipt.
            bundle.driver().receiveResponses();
            result.addProperty("state","native_confirmation_returned");
        } catch(Exception failure) {
            result.addProperty("state","outcome_unknown");result.addProperty("error_type",failure.getClass().getName());result.addProperty("error",bounded(failure.getMessage()));
            try{bundle.driver().receiveResponses();}catch(Exception diagnostic){result.addProperty("native_latched_error",bounded(diagnostic.getMessage()));}
            fenced=true;bundle.driver().disconnect();
        }
        receipts.put(requestId,new JsonParser().parse(result.toString()).getAsJsonObject());return result;
    }
    private static String bounded(String value){return value==null?"":value.substring(0,Math.min(512,value.length()));}
    public boolean isFenced(){return fenced;}
    @Override public synchronized void close(){bundle.driver().disconnect();connected=false;fenced=true;}
}
