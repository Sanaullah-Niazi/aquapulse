// Turning the water on or off (FR-44, FR-51). The button only asks; the
// device answers. We show "Waiting for the valve…" until the device confirms,
// then what really happened, or a clear message (and "Try again") if it failed
// or took too long. A locked valve has no button: the API would refuse anyway (423).
import 'dart:async';

import 'package:flutter/material.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../app_state.dart';
import 'common.dart';

const valveText = {
  'open': 'Water is ON',
  'closed': 'Water is OFF',
  'moving': 'The valve is moving…',
  'fault': 'Valve fault',
};

class ValveControl extends StatefulWidget {
  const ValveControl({super.key, required this.live, required this.unit, this.residentName, this.onChanged});
  final HomeLive live;
  final String unit;

  /// Shown in the owner's confirm step (FR-44).
  final String? residentName;

  /// Called once the device has answered, so the screen reads the new state.
  final VoidCallback? onChanged;

  @override
  State<ValveControl> createState() => _ValveControlState();
}

class _ValveControlState extends State<ValveControl> {
  Command? _command;
  String? _commandId;
  String? _lastAction;
  Object? _error;
  bool _sending = false;
  Timer? _poll;
  StreamSubscription? _sub;

  @override
  void initState() {
    super.initState();
    _commandId = widget.live.pendingCommand?.id;
    if (_commandId != null) _startPolling();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _sub ??= AppScope.read(context).live.events.listen((event) {
      if (event['type'] == 'command' && event['commandId'] == _commandId) _check();
    });
  }

  @override
  void dispose() {
    _poll?.cancel();
    _sub?.cancel();
    super.dispose();
  }

  void _startPolling() {
    _poll?.cancel();
    // Ask every second until the device has answered (the live event is usually faster).
    _poll = Timer.periodic(const Duration(seconds: 1), (_) => _check());
  }

  Future<void> _check() async {
    final id = _commandId;
    if (id == null) return;
    try {
      final command = Command.fromJson(await AppScope.read(context).api.get('/commands/$id') as Map<String, dynamic>);
      if (!mounted || id != _commandId) return;
      setState(() => _command = command);
      if (command.done) {
        _poll?.cancel();
        widget.onChanged?.call();
      }
    } catch (_) {
      // try again on the next tick
    }
  }

  Future<void> _send(String action) async {
    final off = action == 'close';
    final who = widget.residentName == null ? '' : ' (${widget.residentName})';
    final ok = await confirm(
      context,
      off ? 'Turn the water off?' : 'Turn the water on?',
      off
          ? 'Unit ${widget.unit}$who: the valve will close and no water will flow until it is turned on again.'
          : 'Unit ${widget.unit}$who: the valve will open.',
      danger: off,
    );
    if (!ok || !mounted) return;
    setState(() {
      _sending = true;
      _error = null;
      _command = null;
      _commandId = null;
      _lastAction = action;
    });
    try {
      final answer = await AppScope.read(context).api
          .post('/homes/${widget.live.homeId}/valve', {'action': action, 'reason': ''});
      final command = Command.fromJson(answer as Map<String, dynamic>);
      if (!mounted) return;
      setState(() {
        _commandId = command.id;
        _command = command;
      });
      if (!command.done) _startPolling();
    } on ApiError catch (error) {
      setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final live = widget.live;
    if (live.deviceId == null) return const Text('No device is installed in this home yet.');
    final valve = live.valve ?? 'unknown';
    final isOpen = valve == 'open';
    final state = _command?.state;
    final waiting = _commandId != null && (_command == null || !_command!.done);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(isOpen ? Icons.water_drop : Icons.water_drop_outlined, color: isOpen ? Colors.blue : Colors.grey),
            const SizedBox(width: 8),
            Text(valveText[valve] ?? 'Valve state unknown', style: Theme.of(context).textTheme.titleMedium),
          ],
        ),
        const SizedBox(height: 8),
        if (live.lock != null)
          Notice(
            kind: NoticeKind.warn,
            title: '🔒 Locked by the building owner: ${live.lock!.reason}',
            text: 'You cannot change it. Please contact the building owner.',
          )
        else if (!live.online)
          const Notice(kind: NoticeKind.warn, text: 'The device is offline, so the valve cannot be changed now.')
        else
          FilledButton.icon(
            style: isOpen ? FilledButton.styleFrom(backgroundColor: Theme.of(context).colorScheme.error) : null,
            onPressed: waiting || _sending || valve == 'moving' ? null : () => _send(isOpen ? 'close' : 'open'),
            icon: Icon(isOpen ? Icons.power_settings_new : Icons.play_arrow),
            label: Text(isOpen ? 'Turn water off' : 'Turn water on'),
          ),
        if (waiting) const Padding(padding: EdgeInsets.only(top: 8), child: Text('Waiting for the valve to confirm…')),
        if (state == 'succeeded') const Notice(kind: NoticeKind.ok, text: 'Done. The valve confirmed it.'),
        if (state == 'failed')
          Notice(kind: NoticeKind.bad, text: 'The valve could not do it: ${_command!.reason ?? '?'}'),
        if (state == 'rejected') Notice(kind: NoticeKind.bad, text: 'Refused: ${_command!.reason ?? '?'}'),
        if (state == 'timeout')
          Notice(
            kind: NoticeKind.bad,
            text: 'The valve did not answer in time. It may be offline.',
            action: _lastAction == null
                ? null
                : TextButton(onPressed: () => _send(_lastAction!), child: const Text('Try again')),
          ),
        if (_error != null) ErrorBox(error: _error),
      ],
    );
  }
}
