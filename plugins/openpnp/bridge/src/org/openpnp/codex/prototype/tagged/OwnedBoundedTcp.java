/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex.prototype.tagged;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeoutException;
import org.openpnp.codex.prototype.TypedGcodeProfile;
import org.openpnp.machine.reference.driver.TcpCommunications;

/** Private declared protocol transport for an in-process owned responder only. */
final class OwnedBoundedTcp extends TcpCommunications {
    static final int MAX_LINE_BYTES=512, MAX_TOTAL_BYTES=32768, IO_DEADLINE_MS=250;
    private final TypedGcodeProfile.OwnedEndpoint endpoint;
    private final Thread owner=Thread.currentThread();
    private final Object stateLock=new Object();
    private volatile SocketChannel channel;
    private volatile Thread readerOwner;
    private volatile Selector readSelector, writeSelector;
    private volatile String phase="new", fault;
    private volatile String expectedTag, acknowledgedTag;
    private String expectedCommand;
    private boolean writeAttempted;
    private volatile long readBytes, writtenBytes;
    private boolean attempted;

    OwnedBoundedTcp(TypedGcodeProfile.OwnedEndpoint endpoint) {
        endpoint.check(); this.endpoint=endpoint; ipAddress=endpoint.host(); port=endpoint.port();
        lineEndingType=LineEndingType.LF;
    }
    @Override public synchronized void connect() throws Exception {
        requireOwner();endpoint.check(); if(attempted||!phase.equals("new"))throw new IOException("TRANSPORT_ALREADY_ATTEMPTED");
        if(!ipAddress.equals(endpoint.host())||port!=endpoint.port()||lineEndingType!=LineEndingType.LF)throw new IOException("TRANSPORT_CONFIGURATION_CHANGED");
        attempted=true;phase="connecting";
        try {
            channel=SocketChannel.open();channel.configureBlocking(false);readSelector=Selector.open();writeSelector=Selector.open();
            channel.register(writeSelector,SelectionKey.OP_CONNECT);
            long deadline=deadline();boolean connected=channel.connect(new InetSocketAddress(endpoint.host(),endpoint.port()));
            while(!connected){if(!await(writeSelector,deadline))throw new IOException("CONNECT_DEADLINE");connected=channel.finishConnect();}
            if(System.nanoTime()>=deadline)throw new IOException("CONNECT_DEADLINE");
            channel.keyFor(writeSelector).interestOps(SelectionKey.OP_WRITE);channel.register(readSelector,SelectionKey.OP_READ);phase="open";
        }catch(Throwable error){fail("CONNECT_FAILED");throw error;}
    }
    @Override public void disconnect(){synchronized(stateLock){if(fault==null)phase="closed";}closeChannel();}
    void fail(String reason){synchronized(stateLock){if(fault==null)fault=reason;phase="fault";}closeChannel();}
    private void closeChannel(){
        SocketChannel current=channel;if(current!=null)try{current.close();}catch(IOException ignored){}
        Selector reader=readSelector;if(reader!=null){reader.wakeup();try{reader.close();}catch(IOException ignored){}}
        Selector writer=writeSelector;if(writer!=null){writer.wakeup();try{writer.close();}catch(IOException ignored){}}
    }
    boolean healthy(){SocketChannel current=channel;return fault==null&&phase.equals("open")&&current!=null&&current.isOpen()&&current.isConnected();}
    boolean channelOpen(){SocketChannel current=channel;return current!=null&&current.isOpen();}
    String phase(){return phase;} String fault(){return fault;} long readBytes(){return readBytes;} long writtenBytes(){return writtenBytes;}
    synchronized void expect(String tag,String command)throws IOException{
        requireOwner();requireHealthy();if(expectedTag!=null)throw new IOException("COMMAND_ALREADY_PENDING");
        if(!tag.matches("[a-f0-9]{32}:[1-9][0-9]*")||!(command.equals("G21")||command.equals("G90")||command.equals("M115")))throw new IOException("UNSUPPORTED_ARMED_REQUEST");
        expectedTag=tag;expectedCommand=command+" ;codex:"+tag;writeAttempted=false;acknowledgedTag=null;
    }
    void requireAcknowledged(String tag)throws IOException{requireHealthy();if(!tag.equals(acknowledgedTag)||expectedTag!=null)throw new IOException("CORRELATED_ACK_NOT_OBSERVED");}
    private void requireOwner()throws IOException{if(Thread.currentThread()!=owner)throw new IOException("WRONG_TRANSPORT_OWNER");}
    private void requireHealthy()throws IOException{if(!healthy())throw new IOException("TRANSPORT_FENCED"+(fault==null?"":":"+fault));}
    synchronized void bindReader(Thread reader)throws IOException{
        requireOwner();requireHealthy();
        if(readerOwner!=null||reader==null||reader.getState()!=Thread.State.NEW)throw new IOException("READER_ALREADY_BOUND");
        readerOwner=reader;
    }
    @Override public String readLine() throws TimeoutException,IOException {
        if(Thread.currentThread()!=readerOwner)throw new IOException("WRONG_READER_THREAD");
        ByteBuffer one=ByteBuffer.allocate(1);StringBuilder line=new StringBuilder();long frameDeadline=0,idleDeadline=System.nanoTime()+100_000_000L;
        try{
            while(true){
                requireHealthy();one.clear();int count=channel.read(one);
                if(count<0)throw new IOException("PEER_EOF");
                if(count==0){
                    long until=frameDeadline==0?idleDeadline:frameDeadline;
                    if(System.nanoTime()>=until){if(frameDeadline==0)throw new TimeoutException("IDLE_READ_POLL");throw new IOException("FRAME_DEADLINE");}
                    await(readSelector,until);continue;
                }
                if(++readBytes>MAX_TOTAL_BYTES)throw new IOException("RESPONSE_BYTE_CAPACITY");
                if(frameDeadline==0)frameDeadline=deadline();
                int value=one.array()[0]&255;
                if(value=='\n'){
                    if(System.nanoTime()>=frameDeadline)throw new IOException("FRAME_DEADLINE");
                    if(line.length()==0)throw new IOException("EMPTY_RESPONSE_LINE");String answer=line.toString();
                    synchronized(this){String tag=expectedTag;if(tag==null||!writeAttempted||!answer.equals("ok codex:"+tag))throw new IOException("UNEXPECTED_RESPONSE");acknowledgedTag=tag;expectedTag=null;}
                    return answer;
                }
                if(value<32||value>126)throw new IOException("INVALID_RESPONSE_ASCII");
                if(line.length()>=MAX_LINE_BYTES-1)throw new IOException("RESPONSE_LINE_CAPACITY");line.append((char)value);
                if(System.nanoTime()>=frameDeadline)throw new IOException("FRAME_DEADLINE");
            }
        }catch(TimeoutException idle){throw idle;}
        catch(Throwable error){
            // Closing our channel/selector intentionally wakes the native reader. Preserve
            // that explicit closure; unexpected errors while open still fence permanently.
            boolean explicitClose=fault==null&&phase.equals("closed")&&(error instanceof IOException||error instanceof ClosedSelectorException);
            if(fault==null&&!explicitClose)fail(error instanceof IOException?error.getMessage():"READ_FAILED");if(error instanceof IOException)throw(IOException)error;if(error instanceof Error)throw(Error)error;throw new IOException("READ_FAILED",error);}
    }
    @Override public void writeLine(String data)throws IOException{
        synchronized(this){
            requireOwner();requireHealthy();
            if(expectedTag==null||writeAttempted||!java.util.Objects.equals(data,expectedCommand))throw new IOException("WRITE_NOT_ARMED");
            writeAttempted=true;
        }
        writeBounded((data+"\n").getBytes(StandardCharsets.US_ASCII));
    }
    @Override public void writeBytes(byte[] bytes)throws IOException{throw new IOException("RAW_WRITES_UNAVAILABLE");}
    private void writeBounded(byte[] bytes)throws IOException{
        try{
            requireHealthy();if(bytes.length>MAX_LINE_BYTES||writtenBytes+bytes.length>MAX_TOTAL_BYTES)throw new IOException("COMMAND_BYTE_CAPACITY");
            ByteBuffer buffer=ByteBuffer.wrap(bytes);long deadline=deadline();
            while(buffer.hasRemaining()){
                requireHealthy();int count=channel.write(buffer);writtenBytes+=count;
                if(System.nanoTime()>=deadline)throw new IOException("WRITE_DEADLINE");
                if(buffer.hasRemaining()){if(System.nanoTime()>=deadline)throw new IOException("WRITE_DEADLINE");if(!await(writeSelector,deadline))throw new IOException("WRITE_DEADLINE");}
            }
        }catch(Throwable error){fail(error instanceof IOException?error.getMessage():"WRITE_FAILED");if(error instanceof IOException)throw(IOException)error;if(error instanceof Error)throw(Error)error;throw new IOException("WRITE_FAILED",error);}
    }
    @Override public int read()throws IOException{throw new IOException("USE_BOUNDED_LINE_API");}
    @Override public void write(int value)throws IOException{throw new IOException("USE_BOUNDED_LINE_API");}
    private static long deadline(){return System.nanoTime()+IO_DEADLINE_MS*1_000_000L;}
    private static boolean await(Selector selector,long deadline)throws IOException{
        long left=deadline-System.nanoTime();if(left<=0)return false;
        selector.select(Math.max(1,Math.min(100,(left+999999)/1_000_000)));selector.selectedKeys().clear();return System.nanoTime()<deadline;
    }
}
