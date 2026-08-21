import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:chat_ui/main.dart';

void main() {
  testWidgets('App renders chat screen', (WidgetTester tester) async {
    await tester.pumpWidget(const SchoolERPAgentApp());
    await tester.pumpAndSettle();
    expect(find.text('School ERP Assistant'), findsOneWidget);
    expect(find.byType(TextField), findsOneWidget);
    expect(find.byIcon(Icons.send), findsOneWidget);
  });
}