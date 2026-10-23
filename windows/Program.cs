using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices.WindowsRuntime;
using System.Text;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Storage.Streams;

// Digitizer — Windows-native BLE HID touchscreen digitizer for iOS,
// using absolute touch coordinates (no AssistiveTouch, no relative mouse).
// On all iOS, the iPhone must have Accessibility > Zoom enabled (Full Screen, 1x, Controller off) for reports to be delivered as touch — see README.
//
// BLE HID transport ONLY. Advertises HID 0x1812 (multitouch touchscreen via GattServiceProvider).
// Loopback endpoint primitives:
//   contact <x> <y>           Set contact id=0 position (0..10000)
//   contact <id> <x> <y>      Set specific contact id position
//   send                      Flush all contacts as one HID frame
//   release                   Release all contacts
//   release <id>              Release one contact
// Re-pair the iPhone after updating — the descriptor has changed.

static class Uuids
{
    // 16-bit SIG IDs expanded to the Bluetooth base UUID.
    public static Guid Sig(ushort id) => new($"0000{id:x4}-0000-1000-8000-00805f9b34fb");

    public static readonly Guid HidService        = Sig(0x1812);
    public static readonly Guid HidInformation    = Sig(0x2A4A);
    public static readonly Guid ReportMap         = Sig(0x2A4B);
    public static readonly Guid HidControlPoint   = Sig(0x2A4C);
    public static readonly Guid Report            = Sig(0x2A4D);
    public static readonly Guid ProtocolMode      = Sig(0x2A4E);
    public static readonly Guid ReportReference   = Sig(0x2908); // descriptor

    public static readonly Guid BatteryService    = Sig(0x180F);
    public static readonly Guid BatteryLevel      = Sig(0x2A19);
}

internal sealed class ProbeException : Exception
{
    public string Verdict { get; }
    public int ExitCode { get; }

    public ProbeException(string verdict, int exitCode)
        : base(verdict) { Verdict = verdict; ExitCode = exitCode; }

    public ProbeException(string verdict, int exitCode, string message)
        : base(message) { Verdict = verdict; ExitCode = exitCode; }

    public ProbeException(string verdict, int exitCode, string message, Exception innerException)
        : base(message, innerException) { Verdict = verdict; ExitCode = exitCode; }
}

class Digitizer
{
    const byte InputReportId = 1;
    const byte FeatureReportId = 2;
    const int ContactBytes = 6;
    static readonly int ContactSlots = int.TryParse(
        Environment.GetEnvironmentVariable("DIGITIZER_CONTACTS"), out var n) && n >= 1 ? n : 10;
    static readonly int InputReportLength = ContactSlots * ContactBytes + 1;

    // Setup output is plain; once advertising and waiting for a host, every line
    // is timestamped (HH:mm:ss.fff) so a pasted log shows the timing between connect / subscribe / report.
    static volatile bool _ready;
    static string Ts() => _ready ? $"\x1b[90m{DateTime.Now:HH:mm:ss.fff}\x1b[0m  " : "";
    static string C(string tag) => tag switch
    {
        "touch"  => $"\x1b[33m[{tag}]\x1b[0m",
        "hid"    => $"\x1b[32m[{tag}]\x1b[0m",
        "gatt"   => $"\x1b[36m[{tag}]\x1b[0m",
        "adv"    => $"\x1b[35m[{tag}]\x1b[0m",
        "ctl"    => $"\x1b[34m[{tag}]\x1b[0m",
        "notify" => $"\x1b[90m[{tag}]\x1b[0m",
        _        => $"[{tag}]",
    };
    static void Log(string msg) => Console.WriteLine(Ts() + msg);
    static void LogErr(string msg) => Console.Error.WriteLine(Ts() + msg);

    // Compact multitouch descriptor (tested format from testing/hid_lab/compact_multitouch.py).
    // Push/Pop shares globals across N contact collections. Each contact: Tip+Confidence flags,
    // Contact ID, X, Y. Physical 0/0 means use logical bounds (HID 1.11 §6.2.2.7).
    // Confidence is needed on Apple hosts: without it IOHIDEventDriver classifies a Finger
    // collection as a Hand, blocking SpringBoard's edge recognizers.
    static readonly byte[] ReportDescriptor = BuildReportDescriptor();

    static byte[] BuildReportDescriptor()
    {
        byte[] header = {
            0x05, 0x0D,                         // Usage Page (Digitizer)
            0x09, 0x04,                         // Usage (Touch Screen)
            0xA1, 0x01,                         // Collection (Application)
            0x85, InputReportId,                // Report ID (1)
            0x15, 0x00, 0x25, 0x01,             // Logical Min 0, Max 1
            0x35, 0x00, 0x45, 0x00,             // Physical Min 0, Max 0
            0x55, 0x00, 0x65, 0x00,             // Unit Exponent 0, Unit None
            0x75, 0x01, 0x95, 0x02,             // Report Size 1, Count 2
        };
        byte[] contact = {
            0xA4,                               // Push (save globals)
            0x09, 0x22, 0xA1, 0x02,             // Finger, Logical Collection
            0x09, 0x42, 0x09, 0x47, 0x81, 0x02, // Tip Switch + Confidence, Input
            0x95, 0x06, 0x81, 0x03,             // 6-bit padding
            0x09, 0x51, 0x25, 0x7F,             // Contact ID, Max 127
            0x75, 0x08, 0x95, 0x01, 0x81, 0x02, // 8-bit, Input
            0x05, 0x01, 0x09, 0x01, 0xA1, 0x00, // Generic Desktop, Pointer, Physical Col
            0x09, 0x30, 0x09, 0x31,             // X, Y
            0x26, 0x10, 0x27,                   // Logical Max 10000
            0x75, 0x10, 0x95, 0x02, 0x81, 0x02, // 16-bit × 2, Input
            0xC0,                               // End Pointer
            0xB4, 0xC0,                         // Pop, End Finger
        };
        byte[] tail = {
            0x09, 0x54,                         // Usage (Contact Count)
            0x25, (byte)ContactSlots,            // Logical Max
            0x75, 0x08, 0x95, 0x01, 0x81, 0x02, // 8-bit, Input
            0x85, FeatureReportId,              // Report ID (2)
            0x09, 0x55,                         // Usage (Contact Count Maximum)
            0xB1, 0x02,                         // Feature
            0xC0,                               // End Application
        };
        var result = new byte[header.Length + contact.Length * ContactSlots + tail.Length];
        System.Buffer.BlockCopy(header, 0, result, 0, header.Length);
        for (int i = 0; i < ContactSlots; i++)
            System.Buffer.BlockCopy(contact, 0, result, header.Length + i * contact.Length, contact.Length);
        System.Buffer.BlockCopy(tail, 0, result, header.Length + contact.Length * ContactSlots, tail.Length);
        return result;
    }

    // HID Information: bcdHID=0x0111, country=0x00, flags=0x01 (RemoteWake) —
    // mirrors ESP32 _hid->setHidInfo(0x00, 0x01).
    static readonly byte[] HidInformation = { 0x11, 0x01, 0x00, 0x01 };

    GattServiceProvider _hidProvider = null!;
    GattServiceProvider? _batteryProvider;
    GattLocalCharacteristic _input = null!;
    int _subscribers;
    byte[] _lastInputPacket = new byte[InputReportLength];
    byte _featureValue = (byte)Math.Min(ContactSlots, 255);

    // ==== PUBLIC API =====
    // Absolute HID coordinates: 0..10000 (0,0 top-left to 10000,10000 bottom-right). Clamped.
    //   ContactAsync(id, x, y)  Touch down or move contact by id (0..ContactSlots-1)
    //   ReleaseAsync(id)        Release one contact
    //   ReleaseAllAsync()       Release all contacts
    // Gestures, mapping, rotation, and higher-level logic are handled downstream.

    struct ContactState { public bool Down; public ushort X, Y; }

    readonly SemaphoreSlim _txLock = new(1, 1);
    readonly ContactState[] _contacts = new ContactState[ContactSlots];

    public void SetContact(int id, int x, int y)
    {
        var cx = Clamp(x); var cy = Clamp(y);
        bool wasDown = _contacts[id].Down;
        _contacts[id] = new ContactState { Down = true, X = cx, Y = cy };
        if (!wasDown) Log($"{C("touch")} DOWN  id={id} ({cx},{cy})");
    }

    public async Task FlushAsync()
    {
        await _txLock.WaitAsync();
        try
        {
            if (!IsSubscribed)
                throw new InvalidOperationException("no HID client is subscribed");
            await SendFrameAsync();
        }
        finally { _txLock.Release(); }
    }

    public async Task ReleaseAsync(int id)
    {
        await _txLock.WaitAsync();
        try
        {
            if (!_contacts[id].Down) return;
            if (!IsSubscribed)
                throw new InvalidOperationException("no HID client is subscribed");

            var pos = _contacts[id];
            await SendReleaseFrameAsync(id);
            _contacts[id] = default;
            Log($"{C("touch")} UP    id={id} ({pos.X},{pos.Y})");
        }
        finally { _txLock.Release(); }
    }

    public async Task ReleaseAllAsync()
    {
        await _txLock.WaitAsync();
        try
        {
            bool any = false;
            for (int i = 0; i < ContactSlots; i++)
                if (_contacts[i].Down) { any = true; break; }
            if (!any) return;
            if (!IsSubscribed)
                throw new InvalidOperationException("no HID client is subscribed");

            await SendReleaseFrameAsync();
            int released = 0;
            for (int i = 0; i < ContactSlots; i++)
                if (_contacts[i].Down) { _contacts[i] = default; released++; }
            Log($"{C("touch")} UP ALL ({released} contacts)");
        }
        finally { _txLock.Release(); }
    }

    // Pack active contacts from the start of the frame; Contact Count = number of active records.
    byte[] BuildFrame()
    {
        var frame = new byte[InputReportLength];
        int offset = 0;
        int count = 0;
        for (int i = 0; i < ContactSlots; i++)
        {
            if (_contacts[i].Down)
            {
                frame[offset]     = 0x03; // Tip + Confidence
                frame[offset + 1] = (byte)i;
                frame[offset + 2] = (byte)(_contacts[i].X & 0xFF);
                frame[offset + 3] = (byte)(_contacts[i].X >> 8);
                frame[offset + 4] = (byte)(_contacts[i].Y & 0xFF);
                frame[offset + 5] = (byte)(_contacts[i].Y >> 8);
                offset += ContactBytes;
                count++;
            }
        }
        frame[ContactSlots * ContactBytes] = (byte)count;
        return frame;
    }

    // Release frame: active contacts keep flags=0x03, released contacts get flags=0x02 (confidence only).
    byte[] BuildReleaseFrame(int releaseId = -1)
    {
        var frame = new byte[InputReportLength];
        int offset = 0;
        int count = 0;
        for (int i = 0; i < ContactSlots; i++)
        {
            if (_contacts[i].Down)
            {
                bool releasing = (releaseId == -1) || (releaseId == i);
                frame[offset]     = releasing ? (byte)0x02 : (byte)0x03;
                frame[offset + 1] = (byte)i;
                frame[offset + 2] = (byte)(_contacts[i].X & 0xFF);
                frame[offset + 3] = (byte)(_contacts[i].X >> 8);
                frame[offset + 4] = (byte)(_contacts[i].Y & 0xFF);
                frame[offset + 5] = (byte)(_contacts[i].Y >> 8);
                offset += ContactBytes;
                count++;
            }
        }
        frame[ContactSlots * ContactBytes] = (byte)count;
        return frame;
    }

    async Task SendFrameAsync()
    {
        var frame = BuildFrame();
        await NotifyFrameAsync(frame);
    }

    async Task SendReleaseFrameAsync(int releaseId = -1)
    {
        var frame = BuildReleaseFrame(releaseId);
        await NotifyFrameAsync(frame);
    }
    // =========================================================================

    static ushort Clamp(int v) => (ushort)Math.Clamp(v, 0, 10000);

    int ActiveContactCount()
    {
        int n = 0;
        for (int i = 0; i < ContactSlots; i++)
            if (_contacts[i].Down) n++;
        return n;
    }

    string ActiveContactSummary()
    {
        var parts = new List<string>();
        for (int i = 0; i < ContactSlots; i++)
            if (_contacts[i].Down) parts.Add($"{i}:({_contacts[i].X},{_contacts[i].Y})");
        return parts.Count > 0 ? string.Join(" ", parts) : "none";
    }

    // Exit codes (every run emits exactly one [PROBE RESULT] line):
    //   0 HID_0x1812_PUBLISHED
    //   1 UNEXPECTED_ERROR
    //   2 NO_ADAPTER
    //   3 HID_0x1812_DISABLED_BY_POLICY
    //   4 NO_PERIPHERAL_ROLE
    static async Task<int> Main()
    {
        Console.WriteLine("Digitizer — Windows BLE HID touchscreen digitizer");
        var app = new Digitizer();
        try
        {
            await app.StartAsync();
        }
        catch (ProbeException ex)
        {
            LogErr($"[PROBE ERROR] {ex.Message}");
            Log($"[PROBE RESULT] {ex.Verdict}");
            app.Stop();
            return ex.ExitCode;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex);
            Log("[PROBE RESULT] UNEXPECTED_ERROR");
            app.Stop();
            return 1;
        }

        Log("[PROBE RESULT] HID_0x1812_PUBLISHED");

        if (Environment.GetEnvironmentVariable("DIGITIZER_PROBE") == "1")
        {
            Log("PROBE mode: setup succeeded, exiting without interactive loop.");
            app.Stop();
            return 0;
        }

        int port = int.TryParse(Environment.GetEnvironmentVariable("DIGITIZER_PORT"), out var p) ? p : 8760;
        _ = app.RunControlServerAsync(port);

        _ready = true;

        var exit = new TaskCompletionSource();
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; app.Stop(); exit.TrySetResult(); };
        await exit.Task;
        return 0;
    }

    async Task StartAsync()
    {
        var radio = await BluetoothAdapter.GetDefaultAsync();
        if (radio is null)
            throw new ProbeException("NO_ADAPTER", 2,
                "BluetoothAdapter.GetDefaultAsync() returned null — hardware/driver problem. " +
                "Does not guarantee Windows HID (0x1812) support. Check Device Manager, " +
                "Bluetooth service, and system policy, or toggle the adapter to reset it.");
        Log($"adapter: peripheral-role supported = {radio.IsPeripheralRoleSupported}, " +
            $"LE central-role = {radio.IsCentralRoleSupported}");
        if (!radio.IsPeripheralRoleSupported)
            throw new ProbeException("NO_PERIPHERAL_ROLE", 4,
                "The Bluetooth adapter does not support the LE peripheral role.");

        _hidProvider = await CreateServiceAsync(Uuids.HidService, "HID (0x1812)");
        var svc = _hidProvider.Service;

        // HID Information — instrumented read so we can see iOS doing HID discovery.
        await AddReadableAsync(svc, Uuids.HidInformation, HidInformation, "HID Information");
        await AddReadableAsync(svc, Uuids.ReportMap, ReportDescriptor, "Report Map");

        // HID Control Point — write-without-response, host writes 0x00 (suspend) / 0x01.
        var cpParams = new GattLocalCharacteristicParameters
        {
            CharacteristicProperties = GattCharacteristicProperties.WriteWithoutResponse,
        };
        var cp = (await svc.CreateCharacteristicAsync(Uuids.HidControlPoint, cpParams)).Characteristic;
        cp.WriteRequested += async (_, a) =>
        {
            using var d = a.GetDeferral();
            var r = await a.GetRequestAsync();
            var b = r.Value.ToArray();
            Log($"{C("gatt")} HID Control Point written: {(b.Length > 0 ? b[0].ToString() : "?")}");
        };
        Log("  + HID Control Point");

        // Protocol Mode — read + write-without-response, report protocol (0x01).
        byte protocolMode = 0x01;
        var pmParams = new GattLocalCharacteristicParameters
        {
            CharacteristicProperties = GattCharacteristicProperties.Read
                                     | GattCharacteristicProperties.WriteWithoutResponse,
        };
        var pm = (await svc.CreateCharacteristicAsync(Uuids.ProtocolMode, pmParams)).Characteristic;
        pm.ReadRequested += async (s, a) =>
        {
            using var d = a.GetDeferral();
            var r = await a.GetRequestAsync();
            var w = new DataWriter(); w.WriteByte(protocolMode);
            r.RespondWithValue(w.DetachBuffer());
            Log($"{C("gatt")} Protocol Mode read -> {protocolMode}");
        };
        pm.WriteRequested += async (s, a) =>
        {
            using var d = a.GetDeferral();
            var r = await a.GetRequestAsync();
            var b = r.Value.ToArray();
            if (b.Length > 0) protocolMode = b[0];
            Log($"{C("gatt")} Protocol Mode written -> {protocolMode}");
        };
        Log("  + Protocol Mode");

        // Input Report — notify + read, encrypted. Carries the multitouch frame.
        var inParams = new GattLocalCharacteristicParameters
        {
            CharacteristicProperties = GattCharacteristicProperties.Read
                                     | GattCharacteristicProperties.Notify,
            ReadProtectionLevel = GattProtectionLevel.EncryptionRequired,
        };
        var inResult = await svc.CreateCharacteristicAsync(Uuids.Report, inParams);
        if (inResult.Error != BluetoothError.Success)
            throw new InvalidOperationException($"Input Report characteristic: {inResult.Error}");
        _input = inResult.Characteristic;

        // Report Reference descriptor: report ID 1, type 0x01 (Input).
        var inRefParams = new GattLocalDescriptorParameters
        {
            StaticValue = new byte[] { InputReportId, 0x01 }.AsBuffer(),
            ReadProtectionLevel = GattProtectionLevel.EncryptionRequired,
        };
        var inRefResult = await _input.CreateDescriptorAsync(Uuids.ReportReference, inRefParams);
        if (inRefResult.Error != BluetoothError.Success)
            throw new InvalidOperationException($"Input Report Reference descriptor: {inRefResult.Error}");
        _input.SubscribedClientsChanged += OnSubscribersChanged;
        _input.ReadRequested += async (s, a) =>
        {
            using var d = a.GetDeferral();
            var r = await a.GetRequestAsync();
            r.RespondWithValue(_lastInputPacket.AsBuffer());
            Log($"{C("gatt")} Input Report read by host");
        };
        Log($"  + Input Report (ID {InputReportId}, {InputReportLength} bytes, + Report Reference)");

        // Feature Report — read + write, encrypted. Reports the maximum contact count.
        var featParams = new GattLocalCharacteristicParameters
        {
            CharacteristicProperties = GattCharacteristicProperties.Read
                                     | GattCharacteristicProperties.Write,
            ReadProtectionLevel = GattProtectionLevel.EncryptionRequired,
            WriteProtectionLevel = GattProtectionLevel.EncryptionRequired,
        };
        var featResult = await svc.CreateCharacteristicAsync(Uuids.Report, featParams);
        if (featResult.Error != BluetoothError.Success)
            throw new InvalidOperationException($"Feature Report characteristic: {featResult.Error}");
        var feature = featResult.Characteristic;

        var featRefParams = new GattLocalDescriptorParameters
        {
            StaticValue = new byte[] { FeatureReportId, 0x03 }.AsBuffer(), // type 0x03 = Feature
            ReadProtectionLevel = GattProtectionLevel.EncryptionRequired,
        };
        var featRefResult = await feature.CreateDescriptorAsync(Uuids.ReportReference, featRefParams);
        if (featRefResult.Error != BluetoothError.Success)
            throw new InvalidOperationException($"Feature Report Reference descriptor: {featRefResult.Error}");
        feature.ReadRequested += async (s, a) =>
        {
            using var d = a.GetDeferral();
            var r = await a.GetRequestAsync();
            r.RespondWithValue(new byte[] { _featureValue }.AsBuffer());
            Log($"{C("gatt")} Feature Report read -> {_featureValue}");
        };
        feature.WriteRequested += async (s, a) =>
        {
            using var d = a.GetDeferral();
            var r = await a.GetRequestAsync();
            var b = r.Value.ToArray();
            if (b.Length == 1) _featureValue = b[0];
            r.Respond();
            Log($"{C("gatt")} Feature Report written -> {(b.Length > 0 ? b[0].ToString() : "?")}");
        };
        Log($"  + Feature Report (ID {FeatureReportId})");

        // Battery service (independent provider so it can start/stop separately).
        try
        {
            _batteryProvider = await CreateServiceAsync(Uuids.BatteryService, "Battery (0x180F)");
            await AddConstantAsync(_batteryProvider.Service, Uuids.BatteryLevel,
                new byte[] { 100 }, "Battery Level");
            _batteryProvider.StartAdvertising(new GattServiceProviderAdvertisingParameters
                { IsDiscoverable = false, IsConnectable = false });
        }
        catch (Exception ex)
        {
            Log($"  (battery service skipped: {ex.Message})");
        }

        // NOTE: Device Information Service (PnP ID 0x05AC, appearance 0x03C2) is reserved by Windows and cannot be published via GattServiceProvider.
        Log("  ! Device Information / PnP ID: reserved by Windows, omitted");

        _hidProvider.AdvertisementStatusChanged += (s, _) =>
            Log($"{C("adv")} status = {s.AdvertisementStatus}");

        _hidProvider.StartAdvertising(new GattServiceProviderAdvertisingParameters
        {
            IsDiscoverable = true,
            IsConnectable = true,
        });

        Log($"{C("adv")} advertising (status = {_hidProvider.AdvertisementStatus})");
    }

    static async Task<GattServiceProvider> CreateServiceAsync(Guid uuid, string label)
    {
        var result = await GattServiceProvider.CreateAsync(uuid);
        Log($"CreateAsync {label}: {result.Error}");
        if (result.Error == BluetoothError.Success)
            return result.ServiceProvider;

        if (uuid == Uuids.HidService && result.Error == BluetoothError.DisabledByPolicy)
            throw new ProbeException("HID_0x1812_DISABLED_BY_POLICY", 3,
                "Windows returned BluetoothError.DisabledByPolicy for HID service 0x1812.");

        throw new InvalidOperationException($"Windows refused to publish {label}: {result.Error}.");
    }

    static async Task AddConstantAsync(GattLocalService svc, Guid uuid, byte[] value,
        string label, bool encrypted = false)
    {
        var p = new GattLocalCharacteristicParameters
        {
            CharacteristicProperties = GattCharacteristicProperties.Read,
            StaticValue = value.AsBuffer(),
            ReadProtectionLevel = encrypted
                ? GattProtectionLevel.EncryptionRequired
                : GattProtectionLevel.Plain,
        };
        var r = await svc.CreateCharacteristicAsync(uuid, p);
        if (r.Error != BluetoothError.Success)
            throw new InvalidOperationException($"{label}: {r.Error}");
        Log($"  + {label} ({value.Length} bytes)");
    }

    // Readable characteristic backed by an event handler, so every host read is logged.
    static async Task AddReadableAsync(GattLocalService svc, Guid uuid, byte[] value, string label)
    {
        var p = new GattLocalCharacteristicParameters
        {
            CharacteristicProperties = GattCharacteristicProperties.Read,
        };
        var r = await svc.CreateCharacteristicAsync(uuid, p);
        if (r.Error != BluetoothError.Success)
            throw new InvalidOperationException($"{label}: {r.Error}");
        r.Characteristic.ReadRequested += async (s, a) =>
        {
            using var d = a.GetDeferral();
            var req = await a.GetRequestAsync();
            req.RespondWithValue(value.AsBuffer());
            Log($"{C("gatt")} {label} read by host ({value.Length} bytes)");
        };
        Log($"  + {label} ({value.Length} bytes)");
    }

    public bool IsSubscribed => Volatile.Read(ref _subscribers) > 0;



    void OnSubscribersChanged(GattLocalCharacteristic sender, object args)
    {
        int now = sender.SubscribedClients.Count;
        int was = Interlocked.Exchange(ref _subscribers, now);
        if (now > was) Log($"{C("hid")} host SUBSCRIBED to input report ({now} client(s))");
        else if (now < was)
        {
            Log($"{C("hid")} host unsubscribed ({now} client(s))");
            if (now == 0)
            {
                for (int i = 0; i < ContactSlots; i++) _contacts[i] = default;
                _lastInputPacket = new byte[InputReportLength];
            }
        }
    }

    async Task NotifyFrameAsync(byte[] frame)
    {
        _lastInputPacket = frame;

        var sw = System.Diagnostics.Stopwatch.StartNew();
        var results = await _input.NotifyValueAsync(frame.AsBuffer());
        sw.Stop();
        int count = frame[ContactSlots * ContactBytes];
        Log($"{C("notify")} contacts={count} took {sw.ElapsedMilliseconds}ms clients={results.Count}");
        if (results.Count == 0)
            throw new IOException("HID notification reached no subscribed client");

        var failures = results
            .Where(r => r.Status != GattCommunicationStatus.Success || r.BytesSent != frame.Length)
            .Select(r => $"status={r.Status} bytes={r.BytesSent}/{frame.Length}" +
                (r.ProtocolError is byte p ? $" protocol=0x{p:X2}" : ""))
            .ToArray();
        if (failures.Length > 0)
            throw new IOException("HID notification failed: " + string.Join("; ", failures));
    }

    // --- Loopback IPC (127.0.0.1:<port>) -------------------------------
    // Line protocol (replies "ok[...]" or "err <msg>"):
    //   contact <x> <y>           Set contact id=0 position
    //   contact <id> <x> <y>      Set specific contact id position
    //   send                      Flush all contacts as one HID frame
    //   release                   Release all contacts
    //   release <id>              Release one contact
    //   status | ping             Plumbing/health checks
    public async Task RunControlServerAsync(int port)
    {
        var listener = new TcpListener(IPAddress.Loopback, port);
        listener.Start();
        Log($"{C("ctl")} listening on 127.0.0.1:{port}");
        while (true)
        {
            var client = await listener.AcceptTcpClientAsync();
            _ = HandleControlClientAsync(client);
        }
    }

    async Task HandleControlClientAsync(TcpClient client)
    {
        var ep = client.Client.RemoteEndPoint?.ToString();
        Log($"{C("ctl")} client connected {ep}");
        try
        {
            using var _ = client;
            client.NoDelay = true;
            using var stream = client.GetStream();
            using var reader = new StreamReader(stream, Encoding.ASCII);
            var wbuf = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true, NewLine = "\n" };
            string? line;
            while ((line = await reader.ReadLineAsync()) is not null)
            {
                string reply;
                try { reply = await ExecuteCommandAsync(line); }
                catch (Exception ex) { reply = "err " + ex.Message; }
                await wbuf.WriteLineAsync(reply);
            }
        }
        catch (IOException) { }
        Log($"{C("ctl")} client disconnected {ep}");
    }

    public async Task<string> ExecuteCommandAsync(string line)
    {
        var t = line.Replace(',', ' ').Split(' ', StringSplitOptions.RemoveEmptyEntries);
        if (t.Length == 0) return "ok";
        switch (t[0].ToLowerInvariant())
        {
            case "contact" when t.Length == 3:
                SetContact(0, int.Parse(t[1]), int.Parse(t[2]));
                return $"ok 0 {_contacts[0].X} {_contacts[0].Y}";
            case "contact" when t.Length >= 4:
                int cid = int.Parse(t[1]);
                SetContact(cid, int.Parse(t[2]), int.Parse(t[3]));
                return $"ok {cid} {_contacts[cid].X} {_contacts[cid].Y}";
            case "send":
                await FlushAsync();
                return "ok";
            case "release" when t.Length == 1:
                await ReleaseAllAsync();
                return "ok release-all";
            case "release" when t.Length >= 2:
                int rid = int.Parse(t[1]);
                await ReleaseAsync(rid);
                return "ok";
            case "status":
                return $"ok subscribed={IsSubscribed} contacts={ActiveContactCount()} active={ActiveContactSummary()}";
            case "ping":
                return "pong";
            default:
                return "err unknown or bad args: " + line;
        }
    }

    public void Stop()
    {
        try
        {
            if (ActiveContactCount() > 0)
            {
                var frame = BuildReleaseFrame();
                NotifyFrameAsync(frame).Wait(500);
            }
        }
        catch { }
        try { _hidProvider?.StopAdvertising(); } catch { }
        try { _batteryProvider?.StopAdvertising(); } catch { }
    }
}
