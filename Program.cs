using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices.WindowsRuntime;
using System.Text;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Storage.Streams;

// WinBleTouch — Windows-native BLE HID touchscreen digitizer for iOS,
// using absolute touch coordinates (no AssistiveTouch, no relative mouse).
//
// Scope: the BLE HID transport ONLY. It advertises HID 0x1812 with an absolute
// touch-digitizer report map (adapted from a known-good ESP32 design) via
// GattServiceProvider, and exposes exactly:
//     contact(x, y)   send/update the active absolute contact  (0..10000)
//     release()       release the active contact
// over a loopback control endpoint. Coordinate mapping, de-letterboxing,
// rotation, gestures and app logic are the consumer's responsibility.

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

class WinBleTouch
{
    const byte ReportId = 1;

    // Setup output is plain; once advertising and waiting for a host, every line
    // is timestamped (HH:mm:ss.fff) so a pasted log shows the timing between
    // connect / subscribe / report.
    static volatile bool _ready;
    static string Ts() => _ready ? $"{DateTime.Now:HH:mm:ss.fff}  " : "";
    static void Log(string msg) => Console.WriteLine(Ts() + msg);
    static void LogErr(string msg) => Console.Error.WriteLine(Ts() + msg);

    // Exact ESP32 report map (AbsoluteHIDTouch.h reportMap()), REPORT_ID -> 1.
    // Digitizers (0x0D) / Touch Screen (0x04), stylus collection, Tip Switch +
    // In Range + absolute X/Y, logical/physical range 0..10000.
    static readonly byte[] ReportMap =
    {
        0x05, 0x0D, 0x09, 0x04, 0xA1, 0x01, 0x85, ReportId,
        0x09, 0x20, 0xA1, 0x00,
        0x09, 0x42, 0x09, 0x32, 0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x02, 0x81, 0x02,
        0x75, 0x01, 0x95, 0x06, 0x81, 0x01,
        0x05, 0x01, 0x09, 0x01, 0xA1, 0x00,
        0x09, 0x30, 0x09, 0x31,
        0x16, 0x00, 0x00, 0x26, 0x10, 0x27,
        0x36, 0x00, 0x00, 0x46, 0x10, 0x27,
        0x66, 0x00, 0x00, 0x75, 0x10, 0x95, 0x02, 0x81, 0x02,
        0xC0, 0xC0, 0xC0,
    };

    // HID Information: bcdHID=0x0111, country=0x00, flags=0x01 (RemoteWake) —
    // mirrors ESP32 _hid->setHidInfo(0x00, 0x01).
    static readonly byte[] HidInformation = { 0x11, 0x01, 0x00, 0x01 };

    GattServiceProvider _hidProvider = null!;
    GattServiceProvider? _batteryProvider;
    GattLocalCharacteristic _input = null!;
    int _subscribers;
    byte[] _lastPacket = { 0x00, 0, 0, 0, 0 };

    // === PUBLIC SURFACE =======================================================
    // The entire reusable API of this component is two calls. Coordinate
    // contract: x and y are ABSOLUTE HID coordinates in 0..10000 (0,0 = top-left
    // of the digitizer surface, 10000,10000 = bottom-right). Out-of-range values
    // are clamped.
    //
    //   Contact(x, y)  send/update the active absolute contact
    //                  (no active contact -> touch down there;
    //                   active contact    -> move that same contact there)
    //   Release()      release the active contact
    //
    // Taps, holds, drags, freehand drawing, coordinate mapping, de-letterboxing,
    // rotation, gesture synthesis: all downstream. Not this component's job.
    readonly SemaphoreSlim _txLock = new(1, 1);
    bool _contactDown;
    ushort _lastX, _lastY;

    public async Task ContactAsync(int x, int y)
    {
        var cx = Clamp(x); var cy = Clamp(y);
        await _txLock.WaitAsync();
        try
        {
            _lastX = cx; _lastY = cy;
            bool wasDown = _contactDown;
            _contactDown = true;
            await SendAsync(0x03, cx, cy); // Tip Switch + In Range
            if (!wasDown) Log($"[touch] DOWN  ({cx},{cy})");
        }
        finally { _txLock.Release(); }
    }

    public async Task ReleaseAsync()
    {
        await _txLock.WaitAsync();
        try
        {
            if (!_contactDown) return;
            _contactDown = false;
            await SendAsync(0x00, _lastX, _lastY); // contact released
            Log($"[touch] UP    ({_lastX},{_lastY})");
        }
        finally { _txLock.Release(); }
    }
    // =========================================================================

    static ushort Clamp(int v) => (ushort)Math.Clamp(v, 0, 10000);

    // Exit codes (every run emits exactly one [PROBE RESULT] line):
    //   0 HID_0x1812_PUBLISHED            2 NO_ADAPTER
    //   1 UNEXPECTED_ERROR                3 HID_0x1812_DISABLED_BY_POLICY
    //                                     4 NO_PERIPHERAL_ROLE
    static async Task<int> Main()
    {
        Console.WriteLine("WinBleTouch — Windows BLE HID touchscreen digitizer");
        var app = new WinBleTouch();
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

        if (Environment.GetEnvironmentVariable("WINBLETOUCH_PROBE") == "1")
        {
            Log("PROBE mode: setup succeeded, exiting without interactive loop.");
            app.Stop();
            return 0;
        }

        int port = int.TryParse(Environment.GetEnvironmentVariable("WINBLETOUCH_PORT"), out var p) ? p : 8760;
        _ = app.RunControlServerAsync(port);

        if (Console.IsInputRedirected)
        {
            Console.WriteLine("Headless mode — advertising; drive touches via the control server. Ctrl+C to stop.");
            _ready = true;   // from here on, every line is timestamped
            for (int t = 0; ; t++)
            {
                await Task.Delay(5000);
                app.PrintStatus(t);
            }
        }

        Console.WriteLine(
            "\nKeys:  d = contact center   u = release   q = quit\n" +
            $"       (real callers stream `contact <x> <y>` / `release` to 127.0.0.1:{port})\n");
        _ready = true;   // from here on, every line is timestamped

        while (true)
        {
            var key = Console.ReadKey(intercept: true).KeyChar;
            if (key is 'q' or 'Q') break;
            await app.HandleKeyAsync(key);
        }

        app.Stop();
        Log("stopped. Bluetooth stack untouched.");
        return 0;
    }

    async Task StartAsync()
    {
        var radio = await BluetoothAdapter.GetDefaultAsync();
        if (radio is null)
            throw new ProbeException("NO_ADAPTER", 2,
                "BluetoothAdapter.GetDefaultAsync() returned null — hardware/driver problem. " +
                "Says nothing about whether Windows would allow HID 0x1812.");
        Log($"adapter: peripheral-role supported = {radio.IsPeripheralRoleSupported}, " +
            $"LE central-role = {radio.IsCentralRoleSupported}");
        if (!radio.IsPeripheralRoleSupported)
            throw new ProbeException("NO_PERIPHERAL_ROLE", 4,
                "The Bluetooth adapter does not support the LE peripheral role.");

        _hidProvider = await CreateServiceAsync(Uuids.HidService, "HID (0x1812)");
        var svc = _hidProvider.Service;

        // HID Information — instrumented read so we can see iOS doing HID discovery.
        await AddReadableAsync(svc, Uuids.HidInformation, HidInformation, "HID Information");
        await AddReadableAsync(svc, Uuids.ReportMap, ReportMap, "Report Map");

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
            Log($"[gatt] HID Control Point written: {(b.Length > 0 ? b[0].ToString() : "?")}");
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
            Log($"[gatt] Protocol Mode read -> {protocolMode}");
        };
        pm.WriteRequested += async (s, a) =>
        {
            using var d = a.GetDeferral();
            var r = await a.GetRequestAsync();
            var b = r.Value.ToArray();
            if (b.Length > 0) protocolMode = b[0];
            Log($"[gatt] Protocol Mode written -> {protocolMode}");
        };
        Log("  + Protocol Mode");

        // Input Report — notify + read, encrypted. Carries the 5-byte touch packet.
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
        var refParams = new GattLocalDescriptorParameters
        {
            StaticValue = new byte[] { ReportId, 0x01 }.AsBuffer(),
            ReadProtectionLevel = GattProtectionLevel.EncryptionRequired,
        };
        var refResult = await _input.CreateDescriptorAsync(Uuids.ReportReference, refParams);
        if (refResult.Error != BluetoothError.Success)
            throw new InvalidOperationException($"Report Reference descriptor: {refResult.Error}");
        _input.SubscribedClientsChanged += OnSubscribersChanged;
        _input.ReadRequested += async (s, a) =>
        {
            using var d = a.GetDeferral();
            var r = await a.GetRequestAsync();
            r.RespondWithValue(_lastPacket.AsBuffer());
            Log("[gatt] Input Report read by host");
        };
        Log("  + Input Report (+ Report Reference descriptor)");

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

        // NOTE: Device Information Service (PnP ID 0x05AC, appearance 0x03C2) is
        // reserved by Windows and cannot be published via GattServiceProvider.
        Log("  ! Device Information / PnP ID: reserved by Windows, omitted");

        _hidProvider.AdvertisementStatusChanged += (s, _) =>
            Log($"[adv] status = {s.AdvertisementStatus}");

        _hidProvider.StartAdvertising(new GattServiceProviderAdvertisingParameters
        {
            IsDiscoverable = true,
            IsConnectable = true,
        });

        Log($"[adv] advertising HID touchscreen (status = {_hidProvider.AdvertisementStatus})");
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
            Log($"[gatt] {label} read by host ({value.Length} bytes)");
        };
        Log($"  + {label} ({value.Length} bytes)");
    }

    public bool IsSubscribed => Volatile.Read(ref _subscribers) > 0;

    public void PrintStatus(int tick)
    {
        int subs = _input.SubscribedClients.Count;
        var advStatus = _hidProvider.AdvertisementStatus;
        if (subs > 0 || tick % 6 == 0)
            Log($"[status] adv={advStatus} subscribers={subs} contactDown={_contactDown}");
    }

    void OnSubscribersChanged(GattLocalCharacteristic sender, object args)
    {
        int now = sender.SubscribedClients.Count;
        int was = Interlocked.Exchange(ref _subscribers, now);
        if (now > was) Log($"[hid] host SUBSCRIBED to input report ({now} client(s))");
        else if (now < was)
        {
            Log($"[hid] host unsubscribed ({now} client(s))");
            _contactDown = false;
        }
    }

    // 5-byte packet identical to ESP32 send(): [state, xLo, xHi, yLo, yHi].
    async Task SendAsync(byte state, ushort x, ushort y)
    {
        if (_subscribers == 0) { Log("   (no subscriber; report dropped)"); return; }
        var packet = new byte[] { state, (byte)(x & 0xFF), (byte)(x >> 8), (byte)(y & 0xFF), (byte)(y >> 8) };
        _lastPacket = packet;
        await _input.NotifyValueAsync(packet.AsBuffer());
    }

    // --- Interactive keyboard aid (manual testing only) -----------------------
    public async Task HandleKeyAsync(char key)
    {
        switch (key)
        {
            case 'd': await ContactAsync(5000, 5000); break; // contact at center
            case 'u': await ReleaseAsync(); break;           // release
        }
    }

    // --- Loopback control endpoint ------------------------------------------
    // The component's IPC surface, mirroring the public API 1:1. Line protocol
    // on 127.0.0.1:<port>, one command per line:
    //   contact <x> <y>   x,y absolute HID coords 0..10000
    //   release
    //   status | ping     operational plumbing (not touch semantics)
    // Each command replies "ok[ ...]" or "err <msg>" so a caller can pace.
    // No tap/drag/hover/gesture commands — those belong to the consumer.
    public async Task RunControlServerAsync(int port)
    {
        var listener = new TcpListener(IPAddress.Loopback, port);
        listener.Start();
        Log($"[ctl] control endpoint on 127.0.0.1:{port}  (contact / release)");
        while (true)
        {
            var client = await listener.AcceptTcpClientAsync();
            _ = HandleControlClientAsync(client);
        }
    }

    async Task HandleControlClientAsync(TcpClient client)
    {
        var ep = client.Client.RemoteEndPoint?.ToString();
        Log($"[ctl] client connected {ep}");
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
        Log($"[ctl] client disconnected {ep}");
    }

    public async Task<string> ExecuteCommandAsync(string line)
    {
        var t = line.Replace(',', ' ').Split(' ', StringSplitOptions.RemoveEmptyEntries);
        if (t.Length == 0) return "ok";
        switch (t[0].ToLowerInvariant())
        {
            case "contact" when t.Length >= 3:
                await ContactAsync(int.Parse(t[1]), int.Parse(t[2]));
                return $"ok {_lastX} {_lastY}";
            case "release":
                await ReleaseAsync();
                return "ok";
            case "status":
                return $"ok subscribed={IsSubscribed} contactDown={_contactDown} last={_lastX},{_lastY}";
            case "ping":
                return "pong";
            default:
                return "err unknown or bad args: " + line;
        }
    }

    public void Stop()
    {
        try { if (_contactDown) SendAsync(0x00, _lastX, _lastY).Wait(500); } catch { }
        try { _hidProvider?.StopAdvertising(); } catch { }
        try { _batteryProvider?.StopAdvertising(); } catch { }
    }
}
