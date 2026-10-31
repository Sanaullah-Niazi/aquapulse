// The owner's screens arrive in M9 part 2.
import 'package:flutter/material.dart';

import '../../app_state.dart';
import '../../widgets/common.dart';
import '../shared/profile.dart';

class OwnerShell extends StatelessWidget {
  const OwnerShell({super.key});
  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('AquaPulse (owner)'), actions: const [LiveDot()]),
    body: Column(
      children: [
        const Padding(
          padding: EdgeInsets.all(12),
          child: Notice(kind: NoticeKind.info, text: 'Owner screens come in the next update. Use the web app for now.'),
        ),
        Expanded(
          child: Builder(
            builder: (context) => AppScope.of(context).me == null ? const SizedBox() : const ProfileScreen(),
          ),
        ),
      ],
    ),
  );
}
