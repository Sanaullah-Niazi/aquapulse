// Small building blocks used by many screens.
import 'dart:async';

import 'package:flutter/material.dart';

import '../app_state.dart';
import '../format.dart';
import '../live.dart';

/// Loads data from the API, shows "Loading…" and errors, and loads again when
/// [reloadWhen] says a live event changed it, or when you pull down.
class Loader<T> extends StatefulWidget {
  const Loader({super.key, required this.load, required this.builder, this.reloadWhen, this.keyValue});

  final Future<T> Function() load;
  final Widget Function(BuildContext context, T data, Future<void> Function() reload) builder;
  final bool Function(LiveEvent event)? reloadWhen;

  /// Change this (e.g. a chosen range) to load again.
  final Object? keyValue;

  @override
  State<Loader<T>> createState() => _LoaderState<T>();
}

class _LoaderState<T> extends State<Loader<T>> {
  T? _data;
  Object? _error;
  bool _loading = true;
  StreamSubscription<LiveEvent>? _sub;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _sub ??= AppScope.read(context).live.events.listen((event) {
      if (widget.reloadWhen?.call(event) ?? false) _reload();
    });
  }

  @override
  void didUpdateWidget(covariant Loader<T> old) {
    super.didUpdateWidget(old);
    if (old.keyValue != widget.keyValue) _reload();
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  Future<void> _reload() async {
    try {
      final data = await widget.load();
      if (!mounted) return;
      setState(() {
        _data = data;
        _error = null;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final data = _data;
    if (data != null) return widget.builder(context, data, _reload);
    if (_loading) return const LoadingBox();
    return ErrorBox(error: _error, retry: _reload);
  }
}

class LoadingBox extends StatelessWidget {
  const LoadingBox({super.key});
  @override
  Widget build(BuildContext context) => const Padding(
    padding: EdgeInsets.all(24),
    child: Center(child: CircularProgressIndicator(semanticsLabel: 'Loading')),
  );
}

class ErrorBox extends StatelessWidget {
  const ErrorBox({super.key, required this.error, this.retry});
  final Object? error;
  final VoidCallback? retry;
  @override
  Widget build(BuildContext context) => Notice(
    kind: NoticeKind.bad,
    text: '$error',
    action: retry == null ? null : TextButton(onPressed: retry, child: const Text('Try again')),
  );
}

enum NoticeKind { info, ok, warn, bad }

/// A coloured box with an icon and text (never colour alone, for accessibility).
class Notice extends StatelessWidget {
  const Notice({super.key, required this.kind, required this.text, this.action, this.title});
  final NoticeKind kind;
  final String text;
  final String? title;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final (icon, colour) = switch (kind) {
      NoticeKind.info => (Icons.info_outline, scheme.primary),
      NoticeKind.ok => (Icons.check_circle_outline, Colors.green.shade700),
      NoticeKind.warn => (Icons.warning_amber, Colors.orange.shade800),
      NoticeKind.bad => (Icons.error_outline, scheme.error),
    };
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 6),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: colour.withValues(alpha: 0.08),
        border: Border.all(color: colour.withValues(alpha: 0.5)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: colour),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (title != null) Text(title!, style: const TextStyle(fontWeight: FontWeight.bold)),
                Text(text),
                ?action,
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// A number with a label, like "Today: 312 L".
class StatCard extends StatelessWidget {
  const StatCard({super.key, required this.label, required this.value, this.sub, this.icon});
  final String label;
  final Widget value;
  final Widget? sub;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                if (icon != null) ...[Icon(icon, size: 16, color: theme.colorScheme.primary), const SizedBox(width: 4)],
                Expanded(child: Text(label, style: theme.textTheme.labelMedium)),
              ],
            ),
            const SizedBox(height: 4),
            DefaultTextStyle.merge(style: theme.textTheme.titleLarge, child: value),
            if (sub != null) ...[
              const SizedBox(height: 4),
              DefaultTextStyle.merge(style: theme.textTheme.bodySmall, child: sub!),
            ],
          ],
        ),
      ),
    );
  }
}

/// Two cards per row on a phone, more on a tablet.
class StatGrid extends StatelessWidget {
  const StatGrid({super.key, required this.children});
  final List<Widget> children;
  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, box) {
      final columns = box.maxWidth > 700 ? 4 : 2;
      final width = (box.maxWidth - (columns - 1) * 4) / columns;
      return Wrap(
        spacing: 4,
        runSpacing: 4,
        children: [for (final c in children) SizedBox(width: width, child: c)],
      );
    },
  );
}

/// Safe / Check / Unsafe with an icon, not colour only (FR-43, FR-48).
class QualityBadge extends StatelessWidget {
  const QualityBadge(this.quality, {super.key});
  final Quality quality;
  @override
  Widget build(BuildContext context) {
    final (text, icon, colour) = switch (quality) {
      Quality.safe => ('Safe', Icons.check_circle, Colors.green.shade700),
      Quality.check => ('Check', Icons.help, Colors.orange.shade800),
      Quality.unsafe => ('Unsafe', Icons.dangerous, Colors.red.shade700),
      Quality.unknown => ('No reading', Icons.remove_circle_outline, Colors.grey.shade600),
    };
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 16, color: colour),
        const SizedBox(width: 4),
        Text(
          text,
          style: TextStyle(color: colour, fontWeight: FontWeight.w600),
        ),
      ],
    );
  }
}

const billStatusText = {
  'draft': 'Draft',
  'issued': 'To pay',
  'partially_paid': 'Partly paid',
  'paid': 'Paid',
  'overdue': 'Overdue',
  'void': 'Cancelled',
};

class BillStatusChip extends StatelessWidget {
  const BillStatusChip(this.status, {super.key});
  final String status;
  @override
  Widget build(BuildContext context) {
    final colour = switch (status) {
      'paid' => Colors.green.shade700,
      'overdue' => Colors.red.shade700,
      'partially_paid' || 'issued' => Colors.orange.shade800,
      _ => Colors.grey.shade700,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        border: Border.all(color: colour),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(billStatusText[status] ?? status, style: TextStyle(color: colour, fontSize: 12)),
    );
  }
}

/// The green "Live" dot in the top bar: on when the live connection works.
class LiveDot extends StatelessWidget {
  const LiveDot({super.key});
  @override
  Widget build(BuildContext context) {
    final live = AppScope.of(context).live;
    return ValueListenableBuilder(
      valueListenable: live.status,
      builder: (context, status, _) {
        final (text, colour) = switch (status) {
          LiveStatus.live => ('Live', Colors.green),
          LiveStatus.connecting => ('Connecting', Colors.orange),
          LiveStatus.off => ('Offline', Colors.grey),
        };
        return Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Row(
            children: [
              Icon(Icons.circle, size: 10, color: colour),
              const SizedBox(width: 4),
              Text(text, style: const TextStyle(fontSize: 12)),
            ],
          ),
        );
      },
    );
  }
}

/// A page with a heading, scrolls, and pull-down to refresh.
class ScreenList extends StatelessWidget {
  const ScreenList({super.key, required this.children, this.onRefresh});
  final List<Widget> children;
  final Future<void> Function()? onRefresh;
  @override
  Widget build(BuildContext context) {
    final list = ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(12),
      children: children,
    );
    return onRefresh == null ? list : RefreshIndicator(onRefresh: onRefresh!, child: list);
  }
}

class Heading extends StatelessWidget {
  const Heading(this.text, {super.key, this.trailing});
  final String text;
  final Widget? trailing;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: 12, bottom: 6),
    child: Row(
      children: [
        Expanded(child: Text(text, style: Theme.of(context).textTheme.titleMedium)),
        ?trailing,
      ],
    ),
  );
}

/// Ask "Are you sure?" and return true for yes.
Future<bool> confirm(
  BuildContext context,
  String title,
  String text, {
  String yes = 'Yes, do it',
  bool danger = false,
}) async {
  final answer = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(title),
      content: Text(text),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
        FilledButton(
          style: danger ? FilledButton.styleFrom(backgroundColor: Theme.of(context).colorScheme.error) : null,
          onPressed: () => Navigator.pop(context, true),
          child: Text(yes),
        ),
      ],
    ),
  );
  return answer ?? false;
}

void toast(BuildContext context, String text) =>
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
