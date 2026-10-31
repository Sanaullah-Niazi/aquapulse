// Profile (FR-52): my name and phone, password, which alerts also come by
// email, quiet hours, the server, and logging out. Two-factor login is set up
// in the web app (you scan its QR code with this phone); logging in here asks
// for the code when it is on.
import 'package:flutter/material.dart';

import '../../api/client.dart';
import '../../api/models.dart';
import '../../app_state.dart';
import '../../widgets/common.dart';

const ruleNames = {
  'ph_unsafe': 'Water pH unsafe',
  'temp_abnormal': 'Water temperature abnormal',
  'device_offline': 'Device offline',
  'high_usage': 'High daily use',
  'command_failed': 'Valve command failed',
  'sensor_stuck': 'Sensor stuck',
  'tank_low': 'Tank low',
  'tank_overflow': 'Tank overflow risk',
  'dry_run': 'Pump dry-run',
};

class ProfileScreen extends StatelessWidget {
  const ProfileScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    final me = state.me!;
    return ScreenList(
      children: [
        ListTile(
          contentPadding: EdgeInsets.zero,
          leading: const CircleAvatar(child: Icon(Icons.person)),
          title: Text(me.fullName.isEmpty ? me.email : me.fullName),
          subtitle: Text(
            '${me.email} · ${me.isOwner
                ? 'Owner'
                : me.isResident
                ? 'Resident'
                : me.role}',
          ),
        ),
        ListTile(
          contentPadding: EdgeInsets.zero,
          leading: const Icon(Icons.edit),
          title: const Text('Name and phone'),
          onTap: () => _push(context, 'Name and phone', const _NameForm()),
        ),
        ListTile(
          contentPadding: EdgeInsets.zero,
          leading: const Icon(Icons.password),
          title: const Text('Change password'),
          onTap: () => _push(context, 'Change password', const _PasswordForm()),
        ),
        ListTile(
          contentPadding: EdgeInsets.zero,
          leading: const Icon(Icons.notifications_outlined),
          title: const Text('Notifications'),
          subtitle: const Text('Alert emails and quiet hours'),
          onTap: () => _push(context, 'Notifications', const NotificationsForm()),
        ),
        ListTile(
          contentPadding: EdgeInsets.zero,
          leading: const Icon(Icons.verified_user_outlined),
          title: Text(me.twoFactor ? 'Two-factor login is ON' : 'Two-factor login is off'),
          subtitle: const Text('Turn it on or off in the web app (Profile)'),
        ),
        ListTile(
          contentPadding: EdgeInsets.zero,
          leading: const Icon(Icons.dns_outlined),
          title: const Text('Server'),
          subtitle: Text(state.api.baseUrl),
        ),
        const Divider(),
        FilledButton.tonalIcon(
          onPressed: () => state.logout(),
          icon: const Icon(Icons.logout),
          label: const Text('Log out'),
        ),
        TextButton(
          onPressed: () async {
            if (await confirm(context, 'Log out everywhere?', 'This ends your login on every phone and browser.')) {
              await state.logout(everywhere: true);
            }
          },
          child: const Text('Log out on all devices'),
        ),
      ],
    );
  }

  void _push(BuildContext context, String title, Widget body) => Navigator.of(context).push(
    MaterialPageRoute<void>(
      builder: (_) => Scaffold(
        appBar: AppBar(title: Text(title)),
        body: body,
      ),
    ),
  );
}

class _NameForm extends StatefulWidget {
  const _NameForm();
  @override
  State<_NameForm> createState() => _NameFormState();
}

class _NameFormState extends State<_NameForm> {
  late final _name = TextEditingController(text: AppScope.read(context).me!.fullName);
  late final _phone = TextEditingController(text: AppScope.read(context).me!.phone ?? '');
  Object? _error;

  @override
  Widget build(BuildContext context) => ScreenList(
    children: [
      TextField(
        controller: _name,
        decoration: const InputDecoration(labelText: 'Full name'),
      ),
      TextField(
        controller: _phone,
        decoration: const InputDecoration(labelText: 'Phone (optional)'),
        keyboardType: TextInputType.phone,
      ),
      const SizedBox(height: 12),
      if (_error != null) ErrorBox(error: _error),
      FilledButton(
        onPressed: () async {
          final state = AppScope.read(context);
          try {
            await state.api.patch('/me', {
              'full_name': _name.text.trim(),
              'phone': _phone.text.trim().isEmpty ? null : _phone.text.trim(),
            });
            await state.reload();
            if (context.mounted) Navigator.pop(context);
          } on ApiError catch (error) {
            setState(() => _error = error);
          }
        },
        child: const Text('Save'),
      ),
    ],
  );
}

class _PasswordForm extends StatefulWidget {
  const _PasswordForm();
  @override
  State<_PasswordForm> createState() => _PasswordFormState();
}

class _PasswordFormState extends State<_PasswordForm> {
  final _current = TextEditingController();
  final _next = TextEditingController();
  Object? _error;

  @override
  Widget build(BuildContext context) => ScreenList(
    children: [
      TextField(
        controller: _current,
        obscureText: true,
        decoration: const InputDecoration(labelText: 'Current password'),
      ),
      TextField(
        controller: _next,
        obscureText: true,
        decoration: const InputDecoration(
          labelText: 'New password',
          helperText: 'At least 12 characters. Three unrelated words work well.',
        ),
      ),
      const SizedBox(height: 12),
      if (_error != null) ErrorBox(error: _error),
      FilledButton(
        onPressed: () async {
          try {
            await AppScope.read(context).api
                .post('/me/password', {'current_password': _current.text, 'new_password': _next.text});
            if (!context.mounted) return;
            toast(context, 'Password changed. Other devices are logged out.');
            Navigator.pop(context);
          } on ApiError catch (error) {
            setState(() => _error = error);
          }
        },
        child: const Text('Change password'),
      ),
    ],
  );
}

/// Which alerts also come by email, never-by-email types, and quiet hours (FR-52).
/// Critical alerts ignore quiet hours. In the app, every alert always shows.
class NotificationsForm extends StatelessWidget {
  const NotificationsForm({super.key});

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    return Loader<NotificationPrefs>(
      load: () async => NotificationPrefs.fromJson(await state.api.get('/me/notifications') as Map<String, dynamic>),
      builder: (context, prefs, _) => _PrefsEditor(prefs: prefs, role: state.me!.role),
    );
  }
}

class _PrefsEditor extends StatefulWidget {
  const _PrefsEditor({required this.prefs, required this.role});
  final NotificationPrefs prefs;
  final String role;
  @override
  State<_PrefsEditor> createState() => _PrefsEditorState();
}

class _PrefsEditorState extends State<_PrefsEditor> {
  late final NotificationPrefs p = widget.prefs;
  Object? _error;
  bool _saved = false;

  String _hm(String? t) => t == null ? '' : t.substring(0, 5);

  Future<void> _pickTime(bool start) async {
    final current = start ? p.quietFrom : p.quietUntil;
    final parts = (current ?? (start ? '22:00' : '07:00')).split(':').map(int.parse).toList();
    final picked = await showTimePicker(
      context: context,
      initialTime: TimeOfDay(hour: parts[0], minute: parts[1]),
    );
    if (picked == null) return;
    final text = '${picked.hour.toString().padLeft(2, '0')}:${picked.minute.toString().padLeft(2, '0')}';
    setState(() => start ? p.quietFrom = text : p.quietUntil = text);
  }

  @override
  Widget build(BuildContext context) {
    final rules = ruleNames.keys.where(
      (r) => widget.role != 'resident' || !{'tank_low', 'tank_overflow', 'dry_run'}.contains(r),
    );
    final quiet = p.quietFrom != null;
    return ScreenList(
      children: [
        const Text('Every alert always shows in the app. Choose which ones also come by email.'),
        const Heading('Email me alerts from'),
        DropdownButton<String>(
          value: p.emailMin,
          isExpanded: true,
          items: const [
            DropdownMenuItem(value: 'info', child: Text('Info and above (all)')),
            DropdownMenuItem(value: 'warning', child: Text('Warning and above')),
            DropdownMenuItem(value: 'critical', child: Text('Critical only')),
            DropdownMenuItem(value: 'off', child: Text('No alert emails')),
          ],
          onChanged: (v) => setState(() => p.emailMin = v ?? p.emailMin),
        ),
        const Heading('Never email me about'),
        for (final rule in rules)
          CheckboxListTile(
            contentPadding: EdgeInsets.zero,
            title: Text(ruleNames[rule]!),
            value: p.mutedRules.contains(rule),
            onChanged: (on) => setState(() => on == true ? p.mutedRules.add(rule) : p.mutedRules.remove(rule)),
          ),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text('Quiet hours'),
          subtitle: const Text('Non-critical emails wait until the end. Critical alerts always come at once.'),
          value: quiet,
          onChanged: (on) => setState(() {
            p.quietFrom = on ? '22:00' : null;
            p.quietUntil = on ? '07:00' : null;
          }),
        ),
        if (quiet)
          Wrap(
            spacing: 8,
            children: [
              OutlinedButton(onPressed: () => _pickTime(true), child: Text('From ${_hm(p.quietFrom)}')),
              OutlinedButton(onPressed: () => _pickTime(false), child: Text('Until ${_hm(p.quietUntil)}')),
            ],
          ),
        const SizedBox(height: 12),
        if (_error != null) ErrorBox(error: _error),
        if (_saved) const Notice(kind: NoticeKind.ok, text: 'Saved. It applies to the next alert.'),
        FilledButton(
          onPressed: () async {
            try {
              await AppScope.read(context).api.patch('/me/notifications', p.toJson());
              setState(() {
                _saved = true;
                _error = null;
              });
            } on ApiError catch (error) {
              setState(() {
                _error = error;
                _saved = false;
              });
            }
          },
          child: const Text('Save'),
        ),
      ],
    );
  }
}
