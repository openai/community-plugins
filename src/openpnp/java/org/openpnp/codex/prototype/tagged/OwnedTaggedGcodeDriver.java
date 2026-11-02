/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex.prototype.tagged;

import java.io.IOException;
import java.util.*;
import java.util.concurrent.LinkedBlockingQueue;
import org.openpnp.codex.prototype.TypedGcodeProfile;
import org.openpnp.machine.reference.driver.*;
import org.openpnp.model.LengthUnit;
import org.openpnp.spi.HeadMountable;

/** Explicit experimental native GcodeDriver subclass for tagged owned simulation only.
 * Only G21/G90/M115 are admitted. This protocol is not a real firmware compatibility claim. */
public final class OwnedTaggedGcodeDriver extends GcodeDriver {
    public static final String PROFILE="owned-tagged-gcode-v1";
    private final OwnedBoundedTcp transport;
    private final Thread owner=Thread.currentThread();
    private final String generation=UUID.randomUUID().toString().replace("-","");
    private final Map<CommandType,String> templates;
    private volatile ReaderThread ownedReader;
    private volatile String expectedTag, readerUncaughtErrorType;
    private volatile int sequence;
    private boolean connectAttempted;
    private volatile boolean sessionFenced, connectCompleted;

    public OwnedTaggedGcodeDriver(TypedGcodeProfile.OwnedEndpoint endpoint){
        transport=new OwnedBoundedTcp(endpoint);tcp=transport;communicationsType=CommunicationsType.tcp;setName("Owned tagged protocol fixture");
        setUnits(LengthUnit.Millimeters);setTimeoutMilliseconds(200);setConnectWaitTimeMilliseconds(0);setDollarWaitTimeMilliseconds(0);
        setConnectionKeepAlive(false);setSyncInitialLocation(false);setAllowUnhomedMotion(false);setCompressGcode(false);setRemoveComments(false);
        setBackslashEscapedCharactersEnabled(false);setSendOnChangeFeedRate(false);setSendOnChangeAcceleration(false);setSendOnChangeJerk(false);setLoggingGcode(false);
        Map<CommandType,String> values=new LinkedHashMap<>();values.put(CommandType.CONNECT_COMMAND,"G21\nG90");values.put(CommandType.COMMAND_CONFIRM_REGEX,"^ok codex:[0-9a-f]{32}:[1-9][0-9]*$");
        templates=Collections.unmodifiableMap(values);for(CommandType type:CommandType.values())setCommand(null,type,values.get(type));
    }
    @Override public ReferenceDriverCommunications getCommunications(){return transport;}
    private void owner(){if(Thread.currentThread()!=owner)throw new IllegalStateException("WRONG_OWNER_THREAD");}
    private void configured()throws IOException{
        if(tcp!=transport||getCommunicationsType()!=CommunicationsType.tcp||getUnits()!=LengthUnit.Millimeters||getTimeoutMilliseconds()!=200||getConnectWaitTimeMilliseconds()!=0
            ||isConnectionKeepAlive()||isSyncInitialLocation()||isAllowUnhomedMotion()||isCompressGcode()||isRemoveComments()||isBackslashEscapedCharactersEnabled()
            ||isSendOnChangeFeedRate()||isSendOnChangeAcceleration()||isSendOnChangeJerk()||isLoggingGcode())throw new IOException("TAGGED_PROFILE_CHANGED");
        if(commands.size()!=templates.size())throw new IOException("TAGGED_COMMANDS_CHANGED");
        Set<CommandType> seen=EnumSet.noneOf(CommandType.class);
        for(Command command:commands)if(command==null||command.headMountableId!=null||command.type==null||!seen.add(command.type)||!Objects.equals(templates.get(command.type),command.getCommand()))throw new IOException("TAGGED_COMMANDS_CHANGED");
    }
    private void requireConfiguration()throws IOException{
        try{configured();}catch(IOException changed){sessionFenced=true;transport.fail("TAGGED_PROFILE_CHANGED");throw changed;}
    }
    @Override public synchronized void connect()throws Exception{
        owner();requireConfiguration();if(connectAttempted||sessionFenced)throw new IOException("TAGGED_SESSION_NOT_FRESH");connectAttempted=true;
        try{super.connect();transport.requireAcknowledged(generation+":"+sequence);bailOnError();connectCompleted=true;}
        catch(Throwable error){sessionFenced=true;transport.fail("NATIVE_CONNECT_FAILED");throw error;}
    }
    @Override protected void connectThreads(){
        responseQueue=new LinkedBlockingQueue<>(8);receivedConfirmationsQueue=new LinkedBlockingQueue<>(8);reportedLocationsQueue=new LinkedBlockingQueue<>(8);errorResponse=null;
        // The inherited member constructor is protected; the anonymous subtype
        // uses it legally and inherits the pinned native run() unchanged.
        ownedReader=new ReaderThread(){};ownedReader.setName("openpnp-owned-tagged-reader");ownedReader.setDaemon(true);
        ownedReader.setUncaughtExceptionHandler((thread,error)->{readerUncaughtErrorType=error.getClass().getName();sessionFenced=true;transport.fail("READER_UNCAUGHT_ERROR");});
        try{transport.bindReader(ownedReader);}catch(IOException error){sessionFenced=true;transport.fail("READER_BIND_FAILED");throw new IllegalStateException("READER_BIND_FAILED",error);}
        ownedReader.start();
    }
    @Override protected void disconnectThreads(){
        ReaderThread reader=ownedReader;
        if(reader!=null&&reader!=Thread.currentThread())try{reader.join(500);}catch(InterruptedException interrupted){Thread.currentThread().interrupt();transport.fail("READER_JOIN_INTERRUPTED");}
        if(reader!=null&&reader.isAlive())transport.fail("READER_NOT_TERMINATED");
    }
    @Override protected void bailOnError()throws Exception{
        if(sessionFenced||!transport.healthy()||ownedReader==null||!ownedReader.isAlive())throw new IOException("TAGGED_TRANSPORT_UNAVAILABLE");
        if(errorResponse!=null){errorResponse=null;sessionFenced=true;transport.fail("NATIVE_ERROR_RESPONSE");throw new IOException("NATIVE_ERROR_RESPONSE");}
    }
    @Override public String getCommand(HeadMountable mountable,CommandType type){
        if(type==CommandType.COMMAND_CONFIRM_REGEX){String tag=expectedTag;return tag==null?"(?!)":"^ok codex:"+java.util.regex.Pattern.quote(tag)+"$";}
        return super.getCommand(mountable,type);
    }
    @Override public void sendCommand(String command,long timeout)throws Exception{
        owner();requireConfiguration();if(!Arrays.asList("G21","G90","M115").contains(command))throw new IOException("UNSUPPORTED_TAGGED_COMMAND");
        if(timeout!=200||sequence>=64)throw new IOException("TAGGED_REQUEST_BOUND");bailOnError();
        // Drain previous native diagnostic records before arming another request. No wire read is manufactured.
        super.receiveResponses();String tag=generation+":"+(++sequence);expectedTag=tag;
        try{transport.expect(tag,command);super.sendCommand(command+" ;codex:"+tag,timeout);transport.requireAcknowledged(tag);bailOnError();}
        catch(Throwable error){sessionFenced=true;transport.fail("NATIVE_COMMAND_FAILED");throw error;}
        finally{expectedTag=null;}
    }
    @Override protected void processResponse(Line line){
        if(responseQueue.remainingCapacity()==0||receivedConfirmationsQueue.remainingCapacity()==0){sessionFenced=true;transport.fail("NATIVE_RESPONSE_QUEUE_CAPACITY");throw new IllegalStateException("NATIVE_RESPONSE_QUEUE_CAPACITY");}
        super.processResponse(line);
    }
    @Override public synchronized void disconnect(){
        // Native connect/send failure paths also invoke this method on the creating thread.
        owner();sessionFenced=true;super.disconnect();
    }
    public Map<String,Object> protocolSnapshot(){
        Map<String,Object> state=new LinkedHashMap<>();state.put("profile",PROFILE);state.put("native_driver",getClass().getName());state.put("generation",generation);state.put("commands_attempted",sequence);
        state.put("native_connected_flag",connected);state.put("native_connect_completed",connectCompleted);state.put("channel_open",transport.channelOpen());state.put("reader_alive",ownedReader!=null&&ownedReader.isAlive());
        state.put("reader_uncaught_error_type",readerUncaughtErrorType);state.put("transport_phase",transport.phase());state.put("transport_fault",transport.fault());state.put("admission_not_fenced",!sessionFenced&&transport.healthy()&&ownedReader!=null&&ownedReader.isAlive());
        state.put("wire_bytes_read",transport.readBytes());state.put("wire_bytes_written",transport.writtenBytes());state.put("native_response_queue_size",responseQueue.size());state.put("native_confirmation_queue_size",receivedConfirmationsQueue.size());
        state.put("physical_qualification",false);state.put("physical_standstill_verified",false);return Collections.unmodifiableMap(state);
    }
}
