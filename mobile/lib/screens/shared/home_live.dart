// Loads one home's latest reading, valve and lock, and keeps it fresh: new
// readings from the live connection go straight into the screen (FR-48),
// valve answers and device changes load it again.
import 'dart:async';

import 'package:flutter/material.dart';

import '../../api/models.dart';
import '../../app_state.dart';
import '../../widgets/common.dart';

class HomeLiveBuilder extends StatefulWidget {
  const HomeLiveBuilder({super.key, required this.homeId, required this.builder});
  final String homeId;
  final Widget Function(BuildContext context, HomeLive live, Future<void> Function() reload) builder;

  @override
  State<HomeLiveBuilder> createState() => _HomeLiveBuilderState();
}

class _HomeLiveBuilderState extends State<HomeLiveBuilder> {
  HomeLive? _live;
  Object? _error;
  StreamSubscription? _sub;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_sub != null) return;
    _sub = AppScope.read(context).live.events.listen((event) {
      if (event['homeId'] != widget.homeId) return;
      if (event['type'] == 'telemetry' && _live != null) {
        setState(() => _live!.applyLive(event));
      } else if (event['type'] == 'command' || event['type'] == 'device') {
        _load();
      }
    });
    _load();
  }

  @override
  void didUpdateWidget(covariant HomeLiveBuilder old) {
    super.didUpdateWidget(old);
    if (old.homeId != widget.homeId) {
      _live = null;
      _load();
    }
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final data = await AppScope.read(context).api.get('/homes/${widget.homeId}/live');
      if (!mounted) return;
      setState(() {
        _live = HomeLive.fromJson(data as Map<String, dynamic>);
        _error = null;
      });
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final live = _live;
    if (live != null) return widget.builder(context, live, _load);
    if (_error != null) return ErrorBox(error: _error, retry: _load);
    return const LoadingBox();
  }
}
