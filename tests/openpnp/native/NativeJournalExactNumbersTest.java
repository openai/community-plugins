/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import java.math.BigDecimal;
import java.util.*;

/** No native effects. Exercises the exact DTO boundary before all typed replay reducers. */
public final class NativeJournalExactNumbersTest {
    static int checks;
    interface Check {void run()throws Exception;}
    static void need(boolean ok){if(!ok)throw new AssertionError("check "+checks);checks++;}
    static void reject(Check c)throws Exception{try{c.run();throw new AssertionError("invalid accepted");}catch(IllegalArgumentException expected){checks++;}}
    public static void main(String[] ignored)throws Exception {
        for(String text:List.of("1","1.0","1e0","1.00000000000000000")) {
            Map<String,Object> p=NativeJournalJson.parseObject("{\"sequence\":"+text+",\"nested\":{\"count\":"+text+"},\"fraction\":0.125,\"units\":\"mm\"}");
            need(NativeJournalJson.integer(p.get("sequence"),1,2)==1);
            need(NativeJournalJson.copy(p).equals(p));
            need(p.get("fraction").equals(new BigDecimal("0.125")));
            need(((Map<?,?>)p.get("nested")).get("count").equals(BigDecimal.ONE));
        }
        for(String n:List.of("1.00000000000000001","0.99999999999999999","9007199254740991.000000000000001","-0.00000000000000001")) {
            Object value=NativeJournalJson.parseObject("{\"n\":"+n+"}").get("n");
            need(value instanceof BigDecimal && new BigDecimal(n).compareTo((BigDecimal)value)==0);
            reject(()->NativeJournalJson.integer(value,0,9007199254740991L));
            reject(()->NativeJournalJson.integer(NativeJournalJson.copy(Collections.singletonMap("n",value)).get("n"),0,9007199254740991L));
        }
        need(NativeJournalJson.integer(NativeJournalJson.parseObject("{\"n\":9007199254740991}").get("n"),0,9007199254740991L)==9007199254740991L);
        for(String json:List.of("{\"n\":1e309}","{\"n\":1e-10001}","[]"))reject(()->NativeJournalJson.parseObject(json));
        for(Object bad:List.of("1",true,Double.NaN,Double.POSITIVE_INFINITY))reject(()->NativeJournalJson.integer(bad,0,9007199254740991L));
        Map<String,Object> mutable=new LinkedHashMap<>();mutable.put("int",1);mutable.put("double",1.0);mutable.put("long",1L);mutable.put("decimal",new BigDecimal("1.0000"));mutable.put("fraction",0.125);mutable.put("nullable",null);
        Map<String,Object> cloned=NativeJournalJson.copy(mutable);need(!cloned.containsKey("nullable"));need(cloned.get("int").equals(cloned.get("long"))&&cloned.get("long").equals(cloned.get("double"))&&cloned.get("double").equals(cloned.get("decimal")));
        mutable.put("int",2);need(cloned.get("int").equals(BigDecimal.ONE));
        // Native ledger's sequence validator must not round a raw decimal back to 1.
        String id="test-operation",event=id+"/native-ledger-1";
        Map<String,Object> envelope=NativeJournalJson.parseObject("{\"type\":\"native_action_intent\",\"payload\":{\"operation_id\":\""+id+"\",\"event_id\":\""+event+"\",\"ledger_sequence\":1.00000000000000001,\"action_id\":\"a\"}}");
        reject(()->new NativeActionLedger.Replay(id).accept(envelope));
        ((Map<String,Object>)envelope.get("payload")).put("ledger_sequence",BigDecimal.ONE);new NativeActionLedger.Replay(id).accept(envelope);need(true);
        System.out.println("NATIVE_JOURNAL_EXACT_NUMBERS_RESULT {\"passed\":true,\"assertions\":"+checks+",\"native_effects\":0}");
    }
}
