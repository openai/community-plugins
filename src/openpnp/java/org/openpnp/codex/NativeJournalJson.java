/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.math.BigDecimal;
import java.util.*;

/** Bounded journal DTO decoding. Numbers retain decimal value before any typed validation.
 * This is not authentication: an owned journal is still required by the caller. */
final class NativeJournalJson {
    private static final Gson JSON=new Gson();
    private static final int MAX_DEPTH=64, MAX_NODES=1000000, MAX_NUMBER_CHARS=256;
    private NativeJournalJson() {}
    static Map<String,Object> parseObject(String json) {
        Object value=decode(new JsonParser().parse(json),0,new int[1]);
        if(!(value instanceof Map))throw new IllegalArgumentException("Journal object required");
        return (Map<String,Object>)value;
    }
    /** Matches the existing Gson null-field policy, but never goes through Map<Double>. */
    static Map<String,Object> copy(Map<?,?> value) {return value==null?null:parseObject(JSON.toJson(value));}
    static long integer(Object value,long low,long high) {
        if(!(value instanceof Number))throw new IllegalArgumentException("Exact numeric integer required");
        try {long n=decimal(value.toString()).longValueExact();if(n<low||n>high)throw new ArithmeticException();return n;}
        catch(ArithmeticException|NumberFormatException invalid){throw new IllegalArgumentException("Exact integer outside bounds",invalid);}
    }
    private static BigDecimal decimal(String raw) {
        if(raw.length()>MAX_NUMBER_CHARS)throw new IllegalArgumentException("Journal number exceeds bound");
        BigDecimal n=new BigDecimal(raw);
        if(Math.abs((long)n.scale())>10000||!Double.isFinite(n.doubleValue()))throw new IllegalArgumentException("Nonfinite or out-of-bound journal number");
        // Equal JSON numbers such as 1, 1.0 and 1e0 compare equally in retained DTOs.
        return n.stripTrailingZeros();
    }
    private static Object decode(JsonElement value,int depth,int[] count) {
        if(depth>MAX_DEPTH||++count[0]>MAX_NODES)throw new IllegalArgumentException("Journal DTO exceeds bound");
        if(value==null||value.isJsonNull())return null;
        if(value.isJsonObject()){Map<String,Object> out=new LinkedHashMap<>();for(Map.Entry<String,JsonElement> e:value.getAsJsonObject().entrySet())out.put(e.getKey(),decode(e.getValue(),depth+1,count));return out;}
        if(value.isJsonArray()){List<Object> out=new ArrayList<>();for(JsonElement item:value.getAsJsonArray())out.add(decode(item,depth+1,count));return out;}
        JsonPrimitive p=value.getAsJsonPrimitive();if(p.isBoolean())return p.getAsBoolean();if(p.isString())return p.getAsString();if(p.isNumber())return decimal(p.getAsString());throw new IllegalArgumentException("Unsupported journal scalar");
    }
}
