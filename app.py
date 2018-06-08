from flask import Flask, render_template, request
import os
import numpy as np
import pandas as pd
import sys

app = Flask(__name__)

UPLOAD_FOLDER = os.path.basename('uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


def get_requests_from_data(df):
    df = df.replace(np.nan, '', regex=True)

    selections = [s for s in list(df) if '_' in s]

    col_names = ['entity'] + selections + ['type']
    df_requests = df[col_names].copy()

    # Delete duplicate requests (one entity requesting the same other entity multiple times)
    def delete_duplicate_requests(r):
        to_overwrite = r[r.duplicated()].keys().tolist()
        r[to_overwrite] = ''
        return r

    df_requests.apply(delete_duplicate_requests, axis=1)

    main_choices_indices = [i for i, s in enumerate(list(df_requests)) if 'choice_' in s]
    backup_choices_indices = [i for i, s in enumerate(list(df_requests)) if 'backup_' in s]

    # Flatten so each request is its own row
    requests = []
    for _, row in df_requests.iterrows():
        for col in main_choices_indices + backup_choices_indices:
            # print(row)
            # print(col)
            reqd = row[col]
            # print(reqd)
            if reqd == '':
                continue
            try:
                reqd_type = df[df['entity'] == reqd].iloc[0]['type']
            except IndexError:
                print(f'The entity %{reqd} does not exist in the spreadsheet, so it\'s being skipped.')
                continue
            multiplier = 1.0 if col in main_choices_indices else 0.5  # change 0.5 to 0.25?
            requests.append([row['entity'], reqd, multiplier, row['type'], reqd_type])

    df_request_pairs = pd.DataFrame(requests, columns=['requester', 'requested', 'multiplier', 'reqr_type', 'reqd_type'])

    def calculate_choice_score(row_ind):
        """Calculate score for each request and add it as a new column"""
        requester = df_request_pairs.at[row_ind.name, 'requester']
        requested = df_request_pairs.at[row_ind.name, 'requested']
        importance_requester = df[df['entity'] == requester].iloc[0]['importance']
        importance_requested = df[df['entity'] == requested].iloc[0]['importance']

        # print(f'{requester} x {requested} score is {importance_requested * importance_requester}')
        return df_request_pairs.at[row_ind.name, 'multiplier'] * importance_requester * importance_requested

    df_request_pairs['score'] = df_request_pairs.apply(calculate_choice_score, axis=1)

    return df_request_pairs, df_requests, len(main_choices_indices)


def clean_up_requests(df_request_pairs):
    # Delete any company-company and investor-investor meetings
    df_request_pairs['type_total'] = df_request_pairs['reqr_type'] + df_request_pairs['reqd_type']
    df_request_pairs = df_request_pairs[df_request_pairs['type_total'] == 1]
    # print(f'Deleted all same-type meetings:\n{df_request_pairs.head()}')

    # Take care of duplicate meetings (add scores if both ppl wanted to meet)
    df_requests_co_first = df_request_pairs[df_request_pairs['reqr_type'] == 0]
    # NOTE: this means all meetings in the requests df MUST be actual requests. Can't add in all possibilities.
    df_requests_co_first = df_requests_co_first.assign(co_req=True)  # doesn't differentiate backup choices
    df_requests_inv_first = df_request_pairs[df_request_pairs['reqr_type'] == 1]
    df_requests_inv_first = df_requests_inv_first.assign(co_req=False)
    # print(f'Make sure sizes match: {df_requests_co_first.shape[0] + df_requests_inv_first.shape[0]} '
    #       f'= {df_request_pairs.shape[0]}')
    if (df_requests_co_first.shape[0] + df_requests_inv_first.shape[0]) != df_request_pairs.shape[0]:
        print('Error in code - investor + company requests do not match for final dataframe')
        sys.exit()

    # Reverse all inv_first
    df_requests_inv_first.rename(columns={'requested': 'requester', 'requester': 'requested'}, inplace=True)
    df_requests_inv_first.loc[:, ('reqr_type', 'reqd_type')] = [0, 1]
    # combine again
    df_requests_combined = df_requests_co_first.append(df_requests_inv_first)
    # Add scores together; NOTE: this is also summing reqr_type type_total, reqd_type, multiplier cols
    df_requests_combined = df_requests_combined.groupby(['requested', 'requester']).sum().reset_index()

    # Sort input matrix by score.
    df_requests_combined_sorted = df_requests_combined.sort_values(by=['score'], ascending=False)
    df_requests_combined_sorted.rename(columns={'requested': 'entity2', 'requester': 'entity1'}, inplace=True)
    df_requests_combined_sorted = df_requests_combined_sorted.assign(scheduled=False)
    # print(df_requests_combined_sorted.head())
    # Reorder everything based on our preferences (e.g. if two meetings have the same score)

    return df_requests_combined_sorted


def create_schedule(df, num_meetings):
    df_schedule = df[['entity']].copy()
    df_schedule = df_schedule.drop_duplicates(subset=['entity'])

    for ind in range(1, num_meetings + 1):
        df_schedule['mtg' + str(ind)] = ''
        # mtg#_req represents if that person requested that meeting or not
        df_schedule['mtg' + str(ind) + '_req'] = False

    return df_schedule


def schedule_over_unavailability(df_schedule):
    print('Let\'s block out times when attendees are unavailable!')
    more = True
    while more:
        entity = input("What entity (company or investor) has unavailability? "
                       "(Must be spelled and formatted identical to an entry in the \'entity\' column uploaded .csv) \n "
                       "Type \'DONE\' if you're done.\n")

        if entity == 'DONE':
            # more = False
            break

        # Catch error where entity name doesn't match
        try:
            entity_row = df_schedule.index[df_schedule['entity'] == entity].tolist()[0]
        except IndexError:
            print('That entity name doesn\'t match with anything in your spreadsheet. Please check the name and remember that spaces matter!')
            continue

        conflicts_str = input(f"Which meetings can't {entity} make? "
                              f"(format should be \'#\' or \'#,#\'; "
                              f"e.g. if they can't make meetings 1 and 3, enter \'1,3\'\n")
        conflicts = map(int, conflicts_str.split(','))

        try:
            for conflict in conflicts:
                index2 = df_schedule.columns.get_loc('mtg' + str(conflict))
                df_schedule.iloc[entity_row, index2] = "N/A"
        except ValueError:
            print(f'Whoops! I can\'t process {conflicts}, might\'ve been invalid formatting. Let\'s try that again.')
            continue
        except KeyError:
            print(f'Whoops! Looks like one or more of the meetings you entered ({conflicts}) doesn\'t exist according to your csv. Let\'s try that again.')
            continue

        # extra things added day-of
        # df_schedule.iloc[53,1] = "N/A"
        # df_schedule.iloc[53,3] = "N/A"
        # df_schedule.iloc[53,9] = "N/A"
        # df_schedule.iloc[53,11] = "N/A"
        # df_schedule.iloc[43,11] = "N/A"
        # df_schedule.iloc[43,9] = "N/A"

    return df_schedule


def fill_schedule(df_schedule, df_requests_combined_sorted, df_requests, df):
    # includes tie-breaking
    grouped_by_score = df_requests_combined_sorted.groupby('score', sort=False)
    print('If you feel like you\'re doing a lot of tie breaking, you might want to go back into your .csv and use a wider range of values for importance.')

    for name, group in grouped_by_score:
        # pull out all matches (any non matches are moved up to the top of the list)
        duplicates1 = group.duplicated(subset='entity1', keep=False).tolist()
        duplicates2 = group.duplicated(subset='entity2', keep=False).tolist()
        duplicates_boolean = [a or b for a, b in zip(duplicates1, duplicates2)]
        singles_boolean = [not i for i in duplicates_boolean]

        duplicates_inds = [i for (i, v) in zip(group.index.values.tolist(), duplicates_boolean) if v]
        singles_inds = [i for (i, v) in zip(group.index.values.tolist(), singles_boolean) if v]

        # for those, delete if either entity1 or entity2 has no free columns
        for ind in duplicates_inds:
            entity1 = group.loc[ind]['entity1']
            entity2 = group.loc[ind]['entity2']
            entity1_row = df_schedule.index[df['entity'] == entity1].tolist()[0]
            entity2_row = df_schedule.index[df['entity'] == entity2].tolist()[0]
            entity1_open_cols = [i for i, x in enumerate(df_schedule.iloc[entity1_row] == '') if x]
            entity2_open_cols = [i for i, x in enumerate(df_schedule.iloc[entity2_row] == '') if x]

            if len(entity1_open_cols) == 0 | len(entity2_open_cols) == 0:
                duplicates_inds.remove(ind)

        # do a check to get rid of singles within duplicates_inds (caused by deleting those with no free columns)
        duplicates1_2 = group.loc[duplicates_inds, :].duplicated(subset='entity1', keep=False).tolist()
        duplicates2_2 = group.loc[duplicates_inds, :].duplicated(subset='entity2', keep=False).tolist()
        duplicates_boolean_2 = [a or b for a, b in zip(duplicates1_2, duplicates2_2)]
        duplicates_inds = [i for (i, v) in zip(group.index.values.tolist(), duplicates_boolean_2) if v]

        # ask for new order
        if len(duplicates_inds) > 0:
            print('-----------------------------------------------')
            print(group.loc[duplicates_inds, ['entity2', 'entity1']])
            var = input(
                "I need your help breaking ties! Reorder the above meeting pairs with most important mtgs first using the row index (the first number in each row). (format: \'1,2,3\' or \'1\' or \'1,3\') Or type \'SAME\' if it's already in the order you want. \n")

            # todo try to find a way to do a separated sort by entity involved? how to order that?

            try:
                if var == 'SAME':
                    new_order_dupl = duplicates_inds
                else:
                    # Change order in the original df
                    new_order_dupl = [int(s) for s in var.split(',')]  # .strip('[]')
            except ValueError:
                print(f'Oops, looks like there are some formatting errors in what you typed ({var}). I\'m going to exit so we can start over.')
                sys.exit()
        else:
            new_order_dupl = duplicates_inds

        new_order = singles_inds + new_order_dupl
        group = group.reindex(new_order)

        # in order of matrix, schedule meetings.
        for ind, row in group.iterrows():
            # print(ind)
            entity1 = row['entity1']  # all companies are entity1; inv are entity2
            entity2 = row['entity2']
            try:
                entity1_row = df_schedule.index[df['entity'] == entity1].tolist()[0]
                entity2_row = df_schedule.index[df['entity'] == entity2].tolist()[0]
            except IndexError:
                print(f'Oops, looks like one or more of the numbers in {var} might be wrong. I\'m going to exit so we can start over.')
                sys.exit()
            entity1_open_cols = [i for i, x in enumerate(df_schedule.iloc[entity1_row] == '') if x]
            entity2_open_cols = [i for i, x in enumerate(df_schedule.iloc[entity2_row] == '') if x]

            try:
                # open_col = set(entity1_open_cols).intersection(entity2_open_cols).pop()
                # print(df_schedule.iloc[entity1_row])
                # print(df_schedule.iloc[entity2_row])
                open_col = min(set(entity1_open_cols).intersection(entity2_open_cols))

            except AttributeError:
                continue

            except ValueError:
                continue

            df_schedule.iloc[entity1_row, open_col] = entity2
            df_schedule.iloc[entity2_row, open_col] = entity1
            df_requests_combined_sorted.loc[ind, 'scheduled'] = True

            # Mark if the entity requested that meeting; NOTE: includes backups
            entity1_requests = df_requests[df_requests['entity'] == entity1].values
            df_schedule.iloc[entity1_row, open_col + 1] = entity2 in entity1_requests
            entity2_requests = df_requests[df_requests['entity'] == entity2].values
            df_schedule.iloc[entity2_row, open_col + 1] = entity1 in entity2_requests

    return df_schedule, df_requests_combined_sorted


def check_column_names(df):
    if not ('entity' in df.columns):
        print('There is no column labeled \'entity\'. Please fix your spreadsheet and try again. Exiting now.')
        sys.exit()

    if not ('type' in df.columns):
        print('There is no column labeled \'type\'. Please fix your spreadsheet and try again. Exiting now.')
        sys.exit()

    if not ('importance' in df.columns):
        print('There is no column labeled \'importance\'. Please fix your spreadsheet and try again. Exiting now.')
        sys.exit()

    if sum([1 for col in df.columns if 'choice' in col]) == 0:
        print('There are no meeting request columns (should be in the format \'choice_#\'). Please fix your spreadsheet and try again. Exiting now.')
        sys.exit()


@app.route("/")
def index():
    return render_template("upload.html")


@app.route("/upload", methods=['POST'])
def upload():
    msg = None
    if request.method == "POST":
        os.mkdir(app.config['UPLOAD_FOLDER'])
        file = request.files['file']
        filename = file.filename
        print(filename)
        print(file)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(filepath)
        print(filepath)

        try:
            msg = 'Completed: '
            df = pd.read_csv(filename)

            print('STATUS: Checking column names\n')
            check_column_names(df)
            msg = msg + 'checking column names'

            print('STATUS: Taking in data\n')
            df_request_pairs, df_requests, num_meetings = get_requests_from_data(df)
            df_requests_combined_sorted = clean_up_requests(df_request_pairs)
            msg = msg + ', reading data'

            print('STATUS: Setting up schedule\n')
            df_schedule = create_schedule(df, num_meetings)
            msg = msg + ', creating empty schedule'
            msg = msg + '\nNow let\'s block out any unavailability!'
            df_schedule = schedule_over_unavailability(df_schedule)

            df_schedule.to_csv(os.path.join(app.config['UPLOAD_FOLDER'], 'df_schedule'))
            df_requests_combined_sorted.to_csv(os.path.join(app.config['UPLOAD_FOLDER'], 'df_requests_combined_sorted'))

        except:
            msg = 'Something went wrong. Please check your spreadsheet and try again or contact Minna.'



    return render_template("schedule_unavailability.html", msg=msg)


@app.route("/schedule_unavailability", methods=['POST'])
def schedule_unavailability():
    df_schedule = pd.read_csv(os.path.join(app.config['UPLOAD_FOLDER'], 'df_schedule'))
    df_requests_combined_sorted = pd.read_csv(os.path.join(app.config['UPLOAD_FOLDER'], 'df_requests_combined_sorted'))

    print('STATUS: Scheduling (with tie breaks)\n')
    df_schedule, df_requests_combined_sorted = fill_schedule(df_schedule, df_requests_combined_sorted, df_requests, df)

    df_schedule.to_csv(os.path.join(app.config['UPLOAD_FOLDER'], 'df_schedule'))
    df_requests_combined_sorted.to_csv(os.path.join(app.config['UPLOAD_FOLDER'], 'df_requests_combined_sorted'))

    return render_template("break_ties.html")


@app.route("/break_ties", methods=['POST'])
def break_ties():
    df_schedule = pd.read_csv(os.path.join(app.config['UPLOAD_FOLDER'], 'df_schedule'))
    df_requests_combined_sorted = pd.read_csv(os.path.join(app.config['UPLOAD_FOLDER'], 'df_requests_combined_sorted'))

    print('HERE IS THE FINAL SCHEDULE:\n')
    print(df_schedule)
    print(df_requests_combined_sorted)

    df_schedule.to_csv('/Users/minna/Desktop/schedule.csv')
    df_requests_combined_sorted.to_csv('/Users/minna/Desktop/requests.csv')

    print('NEXT STEP: ADD ANY MORE DESIRED MEETINGS MANUALLY AND SEND OUT FINAL SCHEDULES USING MAIL MERGE')

    return render_template("download_schedule.html")  # todo should have download links


def complete():
    os.remove(app.config['UPLOAD_FOLDER'])


if __name__ == "__main__":
    app.run(debug=True)
