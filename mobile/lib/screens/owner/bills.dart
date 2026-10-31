// O-05: bills for a month (FR-45 on Android: view, issue, record payment).
// Make the month's draft bills, issue them all at once, open one to issue,
// void (with a reason) or record a cash / bank payment.
import 'package:flutter/material.dart';

import '../../api/client.dart';
import '../../api/models.dart';
import '../../app_state.dart';
import '../../format.dart' as fmt;
import '../../widgets/common.dart';
import '../shared/bills.dart';

/// "2026-09" for the month before [now].
String lastMonth([DateTime? now]) {
  final n = now ?? DateTime.now();
  final d = DateTime(n.year, n.month - 1);
  return '${d.year}-${d.month.toString().padLeft(2, '0')}';
}

class OwnerBillsScreen extends StatefulWidget {
  const OwnerBillsScreen({super.key, required this.buildingId});
  final String buildingId;
  @override
  State<OwnerBillsScreen> createState() => _OwnerBillsScreenState();
}

class _OwnerBillsScreenState extends State<OwnerBillsScreen> {
  String _month = lastMonth();
  String? _message;
  Object? _error;
  bool _busy = false;
  int _version = 0;

  List<String> get _months {
    final now = DateTime.now();
    return [
      for (var i = 1; i <= 12; i++)
        () {
          final d = DateTime(now.year, now.month - i);
          return '${d.year}-${d.month.toString().padLeft(2, '0')}';
        }(),
    ];
  }

  Future<void> _run(Future<String> Function() call) async {
    setState(() => _busy = true);
    try {
      final message = await call();
      setState(() {
        _message = message;
        _error = null;
        _version++;
      });
    } on ApiError catch (error) {
      setState(() {
        _error = error;
        _message = null;
      });
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final api = AppScope.of(context).api;
    final id = widget.buildingId;
    return Loader<List<BillSummary>>(
      keyValue: '$_month/$_version',
      load: () async => [
        for (final b in await api.get('/buildings/$id/bills?month=$_month') as List)
          BillSummary.fromJson(b as Map<String, dynamic>),
      ],
      reloadWhen: billEvent,
      builder: (context, bills, reload) {
        final drafts = bills.where((b) => b.status == 'draft').length;
        return ScreenList(
          onRefresh: reload,
          children: [
            Row(
              children: [
                const Text('Month '),
                DropdownButton<String>(
                  value: _month,
                  items: [for (final m in _months) DropdownMenuItem(value: m, child: Text(fmt.month('$m-01')))],
                  onChanged: (m) => setState(() {
                    _month = m ?? _month;
                    _message = null;
                    _error = null;
                  }),
                ),
              ],
            ),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                FilledButton(
                  onPressed: _busy
                      ? null
                      : () => _run(() async {
                          final r = await api.post('/buildings/$id/bills/generate', {
                            'month': _month,
                          }) as Map<String, dynamic>;
                          final problems = (r['problems'] as List? ?? const []).join('\n');
                          return '${r['created']} new, ${r['updated']} recalculated, ${r['kept']} already issued and kept, '
                              '${r['issued']} issued at once.${problems.isEmpty ? '' : '\n$problems'}';
                        }),
                  child: const Text('Make draft bills'),
                ),
                OutlinedButton(
                  onPressed: _busy || drafts == 0
                      ? null
                      : () async {
                          if (!await confirm(
                            context,
                            'Issue $drafts bills?',
                            'Residents get them by email. Once issued, a bill never changes (void it to replace it).',
                          )) {
                            return;
                          }
                          await _run(() async {
                            final r = await api.post('/buildings/$id/bills/issue-all', {
                              'month': _month,
                            }) as Map<String, dynamic>;
                            return '${r['issued']} bills issued.';
                          });
                        },
                  child: Text('Issue all drafts ($drafts)'),
                ),
              ],
            ),
            const Padding(
              padding: EdgeInsets.only(top: 6),
              child: Text(
                'Drafts can be checked and made again. Once issued, a bill never changes (void it to replace it).',
                style: TextStyle(fontSize: 12),
              ),
            ),
            if (_message != null) Notice(kind: NoticeKind.ok, text: _message!),
            if (_error != null) ErrorBox(error: _error),
            if (bills.isEmpty) const Padding(padding: EdgeInsets.all(12), child: Text('No bills for this month yet.')),
            for (final b in bills) BillTile(bill: b, showUnit: true),
          ],
        );
      },
    );
  }
}

/// Under a bill, for the owner: issue, void with a reason, record a payment.
class OwnerBillActions extends StatefulWidget {
  const OwnerBillActions({super.key, required this.bill, required this.reload});
  final Bill bill;
  final Future<void> Function() reload;

  static Widget build(BuildContext context, Bill bill, Future<void> Function() reload) =>
      OwnerBillActions(bill: bill, reload: reload);

  @override
  State<OwnerBillActions> createState() => _OwnerBillActionsState();
}

class _OwnerBillActionsState extends State<OwnerBillActions> {
  late final _amount = TextEditingController(text: widget.bill.outstanding);
  final _reference = TextEditingController();
  final _voidReason = TextEditingController();
  String _method = 'cash';
  bool _voiding = false;
  bool _busy = false;
  Object? _error;

  Future<void> _post(String path, [Object? body]) async {
    setState(() => _busy = true);
    try {
      await AppScope.read(context).api.post(path, body);
      if (!mounted) return;
      setState(() {
        _error = null;
        _voiding = false;
      });
      await widget.reload();
    } on ApiError catch (error) {
      setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final bill = widget.bill;
    final open = ['issued', 'partially_paid', 'overdue'].contains(bill.status);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Heading('Owner actions'),
        if (_error != null) ErrorBox(error: _error),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            if (bill.status == 'draft')
              FilledButton(
                onPressed: _busy ? null : () => _post('/bills/${bill.id}/issue'),
                child: const Text('Issue this bill'),
              ),
            if (bill.status != 'void' && !_voiding)
              OutlinedButton(
                onPressed: () => setState(() => _voiding = true),
                child: const Text('Void (cancel) this bill'),
              ),
          ],
        ),
        if (_voiding) ...[
          TextField(
            key: const Key('void-reason'),
            controller: _voidReason,
            maxLength: 300,
            decoration: const InputDecoration(labelText: 'Why? (at least 10 characters)'),
            onChanged: (_) => setState(() {}),
          ),
          Wrap(
            spacing: 8,
            children: [
              FilledButton(
                style: FilledButton.styleFrom(backgroundColor: Theme.of(context).colorScheme.error),
                onPressed: _busy || _voidReason.text.trim().length < 10
                    ? null
                    : () => _post('/bills/${bill.id}/void', {'reason': _voidReason.text.trim()}),
                child: const Text('Void it'),
              ),
              TextButton(onPressed: () => setState(() => _voiding = false), child: const Text('Cancel')),
            ],
          ),
        ],
        if (open && (double.tryParse(bill.outstanding) ?? 0) > 0) ...[
          const Heading('Record a payment'),
          TextField(
            key: const Key('pay-amount'),
            controller: _amount,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(labelText: 'Amount (${bill.currency})'),
          ),
          DropdownButton<String>(
            isExpanded: true,
            value: _method,
            items: const [
              DropdownMenuItem(value: 'cash', child: Text('Cash')),
              DropdownMenuItem(value: 'bank', child: Text('Bank transfer')),
              DropdownMenuItem(value: 'other', child: Text('Other')),
            ],
            onChanged: (m) => setState(() => _method = m ?? _method),
          ),
          TextField(
            controller: _reference,
            decoration: const InputDecoration(labelText: 'Reference (optional)'),
          ),
          const SizedBox(height: 8),
          FilledButton(
            key: const Key('pay-save'),
            onPressed: _busy
                ? null
                : () => _post('/bills/${bill.id}/payments', {
                    'amount': _amount.text.trim(),
                    'method': _method,
                    'reference': _reference.text.trim(),
                  }),
            child: const Text('Record payment'),
          ),
        ],
      ],
    );
  }
}
