/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.JsonObject;
import java.io.IOException;
import java.net.*;
import java.nio.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.regex.*;
import org.openpnp.codex.prototype.TypedGcodeProfile;
import org.openpnp.model.*;

/** Fresh fixed tagged-controller simulator. No caller-selected endpoint, command, or machine XML. */
public final class NativeControllerSimulator {
    private static final Set<String> OPTIONS=Set.of("--config-dir","--token-file","--journal-dir","--sample-root","--port");
    public static void main(String[] args)throws Exception{
        Map<String,String> options=new HashMap<>();
        for(int i=0;i<args.length;i+=2){if(i+1>=args.length||!OPTIONS.contains(args[i])||options.put(args[i],args[i+1])!=null)throw new IllegalArgumentException("Expected unique supported option/value pairs");}
        if(!options.keySet().equals(OPTIONS)||!"0".equals(options.get("--port")))throw new IllegalArgumentException("All fixed launcher options are required; bridge port must be ephemeral");
        Path configRoot=emptyDirectory(options.get("--config-dir")),journal=emptyDirectory(options.get("--journal-dir"));
        Path token=Path.of(options.get("--token-file"));if(Files.isSymbolicLink(token)||!Files.isRegularFile(token,LinkOption.NOFOLLOW_LINKS))throw new IllegalArgumentException("Regular isolated token file required");
        Configuration.initialize(configRoot.toFile());Configuration.get().setSystemUnits(LengthUnit.Millimeters);
        OwnedPeer peer=new OwnedPeer();Bridge bridge;
        try{
            NativeControllerDiagnostic diagnostic=new NativeControllerDiagnostic(Configuration.get(),TypedGcodeProfile.ownedEndpoint(peer.listener.socket()));
            bridge=new Bridge(Configuration.get(),token,journal,Path.of(options.get("--sample-root")),0,true,NativeControllerJournal.PROFILE,null,null,diagnostic);
        }catch(Throwable failure){peer.close();throw failure;}
        Runtime.getRuntime().addShutdownHook(new Thread(()->{
            peer.close();
            // A forced journal append may stall. Keep it off the shutdown hook so this
            // process can exit with preserved uncertainty after the bounded join.
            Thread durableClose=new Thread(()->{try{bridge.call("openpnp_get_status",new JsonObject());bridge.close();}catch(Exception unavailable){System.err.println("OPENPNP_CONTROLLER_EXIT journal preserved; completion/close unavailable");}},"openpnp-controller-durable-close");
            durableClose.setDaemon(true);durableClose.start();
            try{durableClose.join(2000);}catch(InterruptedException interrupted){Thread.currentThread().interrupt();}
            if(durableClose.isAlive())System.err.println("OPENPNP_CONTROLLER_EXIT journal preserved; durable close deadline exceeded");
        },"openpnp-controller-shutdown"));
        bridge.start();System.out.println("OPENPNP_CODEX_READY port="+bridge.getPort()+" upstream="+Bridge.UPSTREAM+" mode="+NativeControllerJournal.PROFILE);
        new CountDownLatch(1).await();
    }
    private static Path emptyDirectory(String value)throws IOException{
        Path p=Path.of(value).toAbsolutePath().normalize();if(Files.isSymbolicLink(p))throw new IOException("Symbolic state directory refused");
        if(Files.exists(p,LinkOption.NOFOLLOW_LINKS)){if(!Files.isDirectory(p,LinkOption.NOFOLLOW_LINKS))throw new IOException("State is not a directory");try(java.util.stream.Stream<Path> files=Files.list(p)){if(files.findAny().isPresent())throw new IOException("Fresh empty controller state required");}}
        else Files.createDirectory(p);return p;
    }
    /** Owns exactly one loopback connection and three fixed tagged acknowledgements. */
    static final class OwnedPeer implements AutoCloseable {
        final ServerSocketChannel listener=ServerSocketChannel.open();
        final Thread worker;volatile SocketChannel channel;volatile boolean closing;
        OwnedPeer()throws IOException{
            try{listener.bind(new InetSocketAddress(InetAddress.getByName("127.0.0.1"),0),1);
                worker=new Thread(this::run,"openpnp-owned-controller-peer");worker.setDaemon(true);worker.start();
            }catch(IOException|RuntimeException|Error failure){try{listener.close();}catch(IOException closeFailure){failure.addSuppressed(closeFailure);}throw failure;}
        }
        private void run(){
            try{
                SocketChannel accepted=listener.accept();synchronized(this){if(closing){accepted.close();return;}channel=accepted;}channel.configureBlocking(false);String generation=null;
                try(Selector selector=Selector.open()){
                    channel.register(selector,SelectionKey.OP_READ);int bytes=0;
                    for(int sequence=1;sequence<=3;sequence++){
                        String line=readLine(selector);bytes+=line.length()+1;if(bytes>512)throw new IOException("Owned peer byte limit");
                        Matcher m=Pattern.compile("^(G21|G90|M115) ;codex:([a-f0-9]{32}):([1-9][0-9]*)$").matcher(line);
                        if(!m.matches()||!List.of("G21","G90","M115").get(sequence-1).equals(m.group(1))||!Integer.toString(sequence).equals(m.group(3)))throw new IOException("Owned peer fixed recipe mismatch");
                        if(generation==null)generation=m.group(2);if(!generation.equals(m.group(2)))throw new IOException("Owned peer generation mismatch");
                        write(selector,ByteBuffer.wrap(("ok codex:"+generation+":"+sequence+"\n").getBytes(StandardCharsets.US_ASCII)));
                    }
                    // A final ACK is not permission for another command. Await bounded peer EOF.
                    ByteBuffer extra=ByteBuffer.allocate(1);long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(2);channel.keyFor(selector).interestOps(SelectionKey.OP_READ);
                    while(!closing&&System.nanoTime()<deadline){int read=channel.read(extra);if(read<0)return;if(read>0)throw new IOException("Owned peer generation already spent");selector.select(50);selector.selectedKeys().clear();}
                    throw new IOException("Owned peer close deadline");
                }
            }catch(Exception failure){if(!closing)System.err.println("OPENPNP_CONTROLLER_PEER_FAILURE "+failure.getClass().getSimpleName());}
            finally{closeResources();}
        }
        private String readLine(Selector selector)throws IOException{
            ByteBuffer one=ByteBuffer.allocate(1);StringBuilder line=new StringBuilder();long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(2);channel.keyFor(selector).interestOps(SelectionKey.OP_READ);
            while(!closing&&System.nanoTime()<deadline){int n=channel.read(one);if(n<0)throw new IOException("Owned peer early EOF");if(n==0){selector.select(50);selector.selectedKeys().clear();continue;}one.flip();int value=one.get()&255;one.clear();if(value==10)return line.toString();if(value<32||value>126||line.length()>=511)throw new IOException("Owned peer framing limit");line.append((char)value);}
            throw new IOException("Owned peer frame deadline");
        }
        private void write(Selector selector,ByteBuffer bytes)throws IOException{
            long deadline=System.nanoTime()+TimeUnit.MILLISECONDS.toNanos(250);channel.keyFor(selector).interestOps(SelectionKey.OP_WRITE);
            while(bytes.hasRemaining()){if(System.nanoTime()>=deadline)throw new IOException("Owned peer write deadline");int written;synchronized(this){if(closing)throw new IOException("Owned peer closing");written=channel.write(bytes);}if(written==0){selector.select(25);selector.selectedKeys().clear();}}
        }
        private synchronized void closeResources(){try{listener.close();}catch(IOException ignored){}SocketChannel current=channel;if(current!=null)try{current.close();}catch(IOException ignored){}}
        public void close(){closing=true;closeResources();if(worker!=Thread.currentThread())try{worker.join(1000);}catch(InterruptedException interrupted){Thread.currentThread().interrupt();}if(worker.isAlive()&&worker!=Thread.currentThread())System.err.println("OPENPNP_CONTROLLER_PEER_CLOSE worker still active; process shutdown required");}
    }
}
