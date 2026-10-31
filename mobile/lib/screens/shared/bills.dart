// R-04 / R-05: my bills, one bill with its lines and payments, the PDF, and
// "Pay online" in Stripe TEST mode (FR-50). Paid amounts update live when the
// owner records a payment or Stripe confirms one.
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:open_filex/open_filex.dart';
import 'package:path_provider/path_provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../api/client.dart';
import '../../api/models.dart';
import '../../app_state.dart';
import '../../format.dart' as fmt;
import '../../widgets/common.dart';

bool billEvent(Map<String, dynamic> e) => e['type'] == 'bill';

class MyBillsScreen extends StatelessWidget {
  const MyBillsScreen({super.key});
  @override
  Widget build(BuildContext context) {
    final api = AppScope.of(context).api;
    return Loader<List<BillSummary>>(
      load: () async => [
        for (final b in await api.get('/me/bills') as List) BillSummary.fromJson(b as Map<String, dynamic>),
      ],
      reloadWhen: billEvent,
      builder: (context, bills, reload) => ScreenList(
        onRefresh: reload,
        children: [
          if (bills.isEmpty) const Text('No bills yet.'),
          for (final bill in bills) BillTile(bill: bill),
        ],
      ),
    );
  }
}

class BillTile extends StatelessWidget {
  const BillTile({super.key, required this.bill, this.showUnit = false});
  final BillSummary bill;
  final bool showUnit;
  @override
  Widget build(BuildContext context) => Card(
    child: ListTile(
      title: Text(showUnit ? 'Unit ${bill.unitNo} · ${fmt.month(bill.month)}' : fmt.month(bill.month)),
      subtitle: Text('${bill.billNo ?? 'Draft'} · due ${fmt.date(bill.dueDate)}'),
      trailing: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [Text(bill.totalText), const SizedBox(height: 4), BillStatusChip(bill.status)],
      ),
      onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => BillScreen(billId: bill.id))),
    ),
  );
}

/// Extra buttons for the owner under a bill (issue, void, record payment): part 2.
typedef BillActions = Widget Function(BuildContext context, Bill bill, Future<void> Function() reload);

class BillScreen extends StatelessWidget {
  const BillScreen({super.key, required this.billId, this.actions});
  final String billId;
  final BillActions? actions;

  static BillActions? ownerActions;

  Future<void> _pdf(BuildContext context, Bill bill) async {
    final api = AppScope.read(context).api;
    try {
      final (name, bytes) = await api.file('/bills/${bill.id}/pdf', 'bill.pdf');
      final dir = await getTemporaryDirectory();
      final file = File('${dir.path}/${name.replaceAll(RegExp(r'[^\w.-]'), '_')}');
      await file.writeAsBytes(bytes);
      final result = await OpenFilex.open(file.path, type: 'application/pdf');
      if (result.type != ResultType.done && context.mounted) {
        toast(context, 'Saved ${file.path}. Install a PDF viewer to open it.');
      }
    } on ApiError catch (error) {
      if (context.mounted) toast(context, error.message);
    }
  }

  Future<void> _pay(BuildContext context, Bill bill) async {
    final api = AppScope.read(context).api;
    try {
      final page = await api.post('/bills/${bill.id}/checkout') as Map<String, dynamic>;
      await launchUrl(Uri.parse(page['url'] as String), mode: LaunchMode.externalApplication);
    } on ApiError catch (error) {
      if (context.mounted) toast(context, error.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final api = AppScope.of(context).api;
    final extra = actions ?? ownerActions;
    final isOwner = AppScope.of(context).me?.isOwner ?? false;
    return Scaffold(
      appBar: AppBar(title: const Text('Bill')),
      body: Loader<Bill>(
        load: () async => Bill.fromJson(await api.get('/bills/$billId') as Map<String, dynamic>),
        reloadWhen: (e) => billEvent(e) && (e['billId'] == null || e['billId'] == billId),
        builder: (context, bill, reload) {
          String m(String amount) => '${fmt.money(amount, bill.total)} ${bill.currency}';
          final toPay = ['issued', 'partially_paid', 'overdue'].contains(bill.status);
          return ScreenList(
            onRefresh: reload,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      '${bill.billNo ?? 'Draft'} · ${fmt.month(bill.month)}',
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                  ),
                  BillStatusChip(bill.status),
                ],
              ),
              Text('Unit ${bill.unitNo} · ${bill.billTo}'),
              Text('Water used: ${fmt.litres(double.tryParse(bill.litres))} · due ${fmt.date(bill.dueDate)}'),
              if (bill.voidReason != null) Notice(kind: NoticeKind.warn, text: 'Cancelled: ${bill.voidReason}'),
              const Heading('Lines'),
              for (final line in bill.lines)
                ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  title: Text(line.description),
                  trailing: Text(m(line.amount)),
                ),
              const Divider(),
              ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: const Text('Total'),
                trailing: Text(bill.totalText, style: const TextStyle(fontWeight: FontWeight.bold)),
              ),
              ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: const Text('Paid'),
                trailing: Text(m(bill.amountPaid)),
              ),
              ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: const Text('Still to pay'),
                trailing: Text(m(bill.outstanding)),
              ),
              if (bill.payments.isNotEmpty) ...[
                const Heading('Payments'),
                for (final p in bill.payments)
                  ListTile(
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                    title: Text(
                      '${fmt.date(p.receivedOn)} · ${p.method}${p.reference.isEmpty ? '' : ' · ${p.reference}'}',
                    ),
                    subtitle: p.reversedAt == null ? null : const Text('Reversed'),
                    trailing: Text(m(p.amount)),
                  ),
              ],
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  if (bill.status != 'draft')
                    OutlinedButton.icon(
                      onPressed: () => _pdf(context, bill),
                      icon: const Icon(Icons.picture_as_pdf),
                      label: const Text('Download PDF'),
                    ),
                  if (!isOwner && toPay && bill.canPayOnline)
                    FilledButton.icon(
                      onPressed: () => _pay(context, bill),
                      icon: const Icon(Icons.credit_card),
                      label: const Text('Pay online (test mode)'),
                    ),
                ],
              ),
              if (!isOwner && toPay && bill.canPayOnline)
                const Padding(
                  padding: EdgeInsets.only(top: 8),
                  child: Text(
                    'Test mode: use card 4242 4242 4242 4242, any future date, any CVC. No real money moves.',
                  ),
                ),
              if (isOwner && extra != null) extra(context, bill, reload),
            ],
          );
        },
      ),
    );
  }
}
