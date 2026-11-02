/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex.prototype.tagged;

import com.google.gson.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.function.BooleanSupplier;
import java.util.regex.*;
import org.openpnp.codex.prototype.TypedGcodeProfile;
import org.openpnp.machine.reference.ReferenceMachine;
import org.openpnp.model.Configuration;

/** Bounded, test-owned protocol peer. Exercises a declared subclass of the pinned native driver. */
public final class TaggedProtocolTest {
    static int assertions;
    static final long START=System.nanoTime();
    static final Gson JSON=new Gson();
    static double ms(){return (System.nanoTime()-START)/1_000_000.0;}
    static void check(boolean value,String message){assertions++;if(!value)throw new AssertionError(message);}
    static void await(BooleanSupplier condition,long millis)throws InterruptedException {
        long end=System.nanoTime()+TimeUnit.MILLISECONDS.toNanos(millis);
        while(!condition.getAsBoolean()&&System.nanoTime()<end)Thread.sleep(5);
    }
    static final class Controller implements AutoCloseable {
        final String mode;
        final ServerSocket listener=new ServerSocket();
        final Thread worker;
        final List<String> commands=Collections.synchronizedList(new ArrayList<>());
        final List<JsonObject> events=Collections.synchronizedList(new ArrayList<>());
        volatile Socket socket;
        volatile boolean closing,peerEof;
        volatile Throwable failure;
        volatile int accepted,commandBytes,responseBytes;
        String generation,lastTag;
        Controller(String mode)throws Exception {
            this.mode=mode;listener.bind(new InetSocketAddress(InetAddress.getByName("127.0.0.1"),0),1);
            worker=new Thread(this::run,"tagged-owned-responder");worker.setDaemon(true);worker.start();
        }
        synchronized void event(String kind,String value){
            JsonObject e=new JsonObject();e.addProperty("at_ms",ms());e.addProperty("kind",kind);e.addProperty("text",value);events.add(e);
        }
        synchronized void send(byte[] data)throws IOException {
            if(data.length>1024||responseBytes+data.length>16384)throw new IOException("FIXTURE_RESPONSE_BOUND");
            responseBytes+=data.length;event("response-base64",Base64.getEncoder().encodeToString(data));
            socket.getOutputStream().write(data);socket.getOutputStream().flush();
        }
        void send(String text)throws IOException{send(text.getBytes(StandardCharsets.US_ASCII));}
        void eof()throws IOException{event("server-output-shutdown","");socket.shutdownOutput();}
        void run(){try{
            socket=listener.accept();accepted++;socket.setSoTimeout(50);event("accepted","");
            ByteArrayOutputStream line=new ByteArrayOutputStream();
            while(!closing){
                int value;try{value=socket.getInputStream().read();}catch(SocketTimeoutException wait){continue;}
                if(value<0){peerEof=true;event("peer-eof","");break;}
                if(++commandBytes>16384)throw new IOException("FIXTURE_COMMAND_BOUND");
                if(value!='\n'){
                    if(value<32||value>126||line.size()>=511)throw new IOException("FIXTURE_COMMAND_FRAMING");line.write(value);continue;
                }
                String command=new String(line.toByteArray(),StandardCharsets.US_ASCII);line.reset();commands.add(command);event("command",command);
                Matcher m=Pattern.compile("^(G21|G90|M115) ;codex:([a-f0-9]{32}):([1-9][0-9]*)$").matcher(command);
                if(!m.matches())throw new IOException("FIXTURE_UNEXPECTED_COMMAND");
                if(generation==null)generation=m.group(2);
                if(!generation.equals(m.group(2))||Integer.parseInt(m.group(3))!=commands.size())throw new IOException("FIXTURE_TAG_ORDER");
                String tag=m.group(2)+":"+m.group(3),verb=m.group(1);
                if(commands.size()<=2){
                    if(!verb.equals(commands.size()==1?"G21":"G90"))throw new IOException("FIXTURE_STARTUP_ORDER");
                    send("ok codex:"+tag+"\n");lastTag=tag;continue;
                }
                if(!verb.equals("M115"))throw new IOException("FIXTURE_NON_DIAGNOSTIC_COMMAND");
                switch(mode){
                    case "normal":case "reader-error":case "raw-api":case "capacity":case "wrong-thread":case "changed-profile":case "unsupported-command":
                        send("ok codex:"+tag+"\n");break;
                    case "old-ack":send("ok codex:"+lastTag+"\n");break;
                    case "wrong-generation":send("ok codex:"+(generation.charAt(0)=='0'?"1":"0")+generation.substring(1)+":"+m.group(3)+"\n");break;
                    case "future-sequence":send("ok codex:"+generation+":4\n");break;
                    case "duplicate-ack":send("ok codex:"+tag+"\nok codex:"+tag+"\n");break;
                    case "reset":send("start\nok codex:"+tag+"\n");break;
                    case "large-line":send("x".repeat(512)+"\n");break;
                    case "cr":send("ok codex:"+tag+"\r\n");break;
                    case "blank":send("\n");break;
                    case "non-ascii":send(new byte[]{(byte)0xff,10});break;
                    case "incomplete":send("ok codex:"+tag);break;
                    case "eof-command":eof();break;
                    case "eof-idle":throw new IOException("M115_AFTER_IDLE_EOF");
                    default:throw new IOException("FIXTURE_UNKNOWN_MODE");
                }
                lastTag=tag;
            }
        }catch(Throwable e){if(!closing)failure=e;}}
        JsonObject snapshot(){
            JsonObject s=new JsonObject();s.addProperty("accepted_connections",accepted);s.addProperty("command_bytes",commandBytes);
            s.addProperty("response_bytes",responseBytes);s.addProperty("peer_eof",peerEof);
            synchronized(commands){s.add("commands",JSON.toJsonTree(commands));}synchronized(events){s.add("events",JSON.toJsonTree(events));}
            if(failure!=null)s.addProperty("failure",failure.toString());return s;
        }
        public void close()throws Exception{
            closing=true;if(socket!=null)socket.close();listener.close();worker.join(1000);
            check(!worker.isAlive(),"owned responder terminated");if(failure!=null)throw new AssertionError("responder failure",failure);
        }
    }
}
